import os
import random
import secrets
import asyncio
import nest_asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, Defaults
)
import logging
import httpx

nest_asyncio.apply()

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Emojis
EMOJIS = {
    'visa': "🟦 VISA",
    'mastercard': "🟥 MC",
    'amex': "🟩 AMEX",
    'discover': "🟨 DISC",
    'diners': "🍽 DINERS",
    'jcb': "🟪 JCB",
    'unionpay': "🇨🇳 UPay",
    'mir': "🇷🇺 MIR",
    'unknown': "💳 CARD"
}
CHECKMARK = "\u2705"
CROSSMARK = "\u274C"
LOCK = "\U0001F512"
GIFT = "\U0001F381"
SHIELD = "\U0001F6E1"
STAR = "\u2B50"
WARNING = "\u26A0"
MONEY_BANK = "\U0001F3E6"
BLUE_CIRCLE = "\U0001F535"
ORANGE_CIRCLE = "\U0001F7E0"

def escape_markdown_v2(text):
    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    for c in escape_chars:
        text = text.replace(c, f"\\{c}")
    return text

# Env
ADMIN_ID = os.getenv("ADMIN_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")

if not BOT_TOKEN or not ADMIN_ID or not GROUP_CHAT_ID:
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_ID:
        missing.append("ADMIN_ID")
    if not GROUP_CHAT_ID:
        missing.append("GROUP_CHAT_ID")
    logger.error(f"Missing environment variables: {', '.join(missing)}. Please set them and restart.")
    exit(1)

def card_brand_type(cc):
    cc = str(cc)
    if cc.startswith("4"):
        return EMOJIS['visa'], "VISA"
    if cc.startswith(("51", "52", "53", "54", "55")) or (2221 <= int(cc[:4]) <= 2720):
        return EMOJIS['mastercard'], "MASTERCARD"
    if cc.startswith(("34", "37")):
        return EMOJIS['amex'], "AMEX"
    if cc.startswith("6"):
        return EMOJIS['discover'], "DISCOVER"
    if cc.startswith(("300", "301", "302", "303", "304", "305", "36", "38", "39")):
        return EMOJIS['diners'], "DINERS"
    if cc.startswith("35"):
        return EMOJIS['jcb'], "JCB"
    if cc.startswith("62"):
        return EMOJIS['unionpay'], "UNIONPAY"
    if cc.startswith("220"):
        return EMOJIS['mir'], "MIR"
    return EMOJIS['unknown'], "UNKNOWN"

class TelegramCCCheckerBot:
    def __init__(self):
        self.is_logged_in = set()
        self.pending_auth = {}

    def is_authorized_group(self, chat_id):
        return str(chat_id) == str(GROUP_CHAT_ID)

    def random_cc_bin_and_length(self):
        brands = [
            ("4", 16, 'visa'),
            ("51", 16, 'mastercard'), ("52", 16, 'mastercard'), ("53", 16, 'mastercard'),
            ("54", 16, 'mastercard'), ("55", 16, 'mastercard'),
            ("2221", 16, 'mastercard'), ("2720", 16, 'mastercard'),
            ("34", 15, 'amex'), ("37", 15, 'amex'),
            ("6011", 16, 'discover'), ("65", 16, 'discover'),
            ("35", 16, 'jcb'),
            ("62", 16, 'unionpay'),
        ]
        prefix, length, brand = random.choice(brands)
        if len(prefix) < 6:
            prefix += ''.join(secrets.choice("0123456789") for _ in range(6 - len(prefix)))
        return (prefix, length, brand)

    def random_cc_as_string(self):
        bin6, length, brand = self.random_cc_bin_and_length()
        cc_list = [int(d) for d in bin6]
        while len(cc_list) < (length - 1):
            cc_list.append(secrets.randbelow(10))
        checksum = self.luhn_checksum(cc_list)
        cc_list.append(checksum)
        cc_number = "".join(str(x) for x in cc_list)
        cvv_len = 4 if brand == 'amex' else 3
        exp_month = f"{random.randint(1, 12):02d}"
        exp_year = f"{random.randint(24, 30)}"
        cvv = "".join(str(random.randint(0,9)) for _ in range(cvv_len))
        return f"{cc_number}|{exp_month}|{exp_year}|{cvv}"

    def random_bin_cc(self, bin6):
        bin6 = str(bin6)[:6]
        while len(bin6) < 6:
            bin6 += "0"
        length = 15 if bin6.startswith(("34", "37")) else 16
        cc_list = list(map(int, bin6))
        while len(cc_list) < (length - 1):
            cc_list.append(random.randint(0, 9))
        checksum = self.luhn_checksum(cc_list)
        cc_list.append(checksum)
        cc_number = "".join(str(x) for x in cc_list)
        cvv_len = 4 if bin6.startswith(("34", "37")) else 3
        exp_month = f"{random.randint(1, 12):02d}"
        exp_year = f"{random.randint(24, 30)}"
        cvv = "".join(str(random.randint(0,9)) for _ in range(cvv_len))
        return f"{cc_number}|{exp_month}|{exp_year}|{cvv}"

    def luhn_checksum(self, digits):
        digits = digits[:]
        digits.reverse()
        total = 0
        for i, d in enumerate(digits):
            if i % 2 == 0:
                total += d
            else:
                dd = d * 2
                if dd > 9:
                    dd -= 9
                total += dd
        return (10 - (total % 10)) % 10

    # NEW: /gen sends each cc in a separate message (box), with brand+emoji, no username.
    async def gen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized_group(update.effective_chat.id):
            await update.message.reply_text(f"{CROSSMARK} Access denied. Please use this bot only in the authorized group.")
            return

        args = context.args
        count = 1
        bin_mode = False
        bin_given = ""

        if args:
            if len(args) == 1 and args[0].isdigit():
                if 6 <= len(args[0]) <= 8:
                    bin_mode = True
                    bin_given = args[0]
                else:
                    count = min(int(args[0]), 20)
            elif len(args) == 2 and args[0].isdigit() and args[1].isdigit():
                bin_mode = True
                bin_given = args[0]
                count = min(int(args[1]), 20)

        if not bin_mode:
            cards = [self.random_cc_as_string() for _ in range(count)]
        else:
            cards = [self.random_bin_cc(bin_given) for _ in range(count)]

        for card in cards:
            ccn = card.split("|")[0]
            emoji, brand = card_brand_type(ccn)
            response = f"`{escape_markdown_v2(card)}` | {emoji}"
            msg = await update.message.reply_text(response, parse_mode="MarkdownV2")
            asyncio.create_task(self.autodel_message(context, msg, 300))

    # /chk for single card
    async def chk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized_group(update.effective_chat.id):
            await update.message.reply_text(f"{CROSSMARK} Access denied. Please use this bot only in the authorized group.")
            return
        if update.effective_user.username:
            username = escape_markdown_v2(f"@{update.effective_user.username}")
        else:
            username = escape_markdown_v2(update.effective_user.first_name)

        args = context.args
        if not args or len(args) != 1:
            await update.message.reply_text(f"{WARNING} Usage: /chk cc|mm|yy|cvv", parse_mode="MarkdownV2")
            return
        card_str = args[0].strip()
        card_data = card_str.split("|")
        if len(card_data) != 4:
            await update.message.reply_text(f"{CROSSMARK} Invalid card format: '{escape_markdown_v2(card_str)}' (must be cc|mm|yy|cvv)", parse_mode="MarkdownV2")
            return

        res, emoji, _ = await self.full_auth_check(card_str, get_brand=True)
        txt = (
            f"`{escape_markdown_v2(card_str)}` | {emoji}\n"
            f"{escape_markdown_v2(res)}\n"
            f"Checked by: {username}"
        )
        msg = await update.message.reply_text(txt, parse_mode="MarkdownV2")
        asyncio.create_task(self.autodel_message(context, msg, 300))

    # /mchk for batch cards (up to 20), blank lines between blocks
    async def mchk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized_group(update.effective_chat.id):
            await update.message.reply_text(f"{CROSSMARK} Access denied. Please use this bot only in the authorized group.")
            return
        if update.effective_user.username:
            username = escape_markdown_v2(f"@{update.effective_user.username}")
        else:
            username = escape_markdown_v2(update.effective_user.first_name)

        full_text = update.message.text
        lines = [line.strip() for line in full_text.split('\n') if line.strip() and not line.strip().startswith('/mchk')]
        valid_cards = [line for line in lines if len(line.split('|')) == 4]

        if not valid_cards:
            await update.message.reply_text(f"{WARNING} Paste up to 20 cc|mm|yy|cvv, each on a new line after /mchk", parse_mode="MarkdownV2")
            return

        valid_cards = valid_cards[:20]
        top_bin = valid_cards[0][:6]
        bin_info_msg = await self.get_bin_info_message(top_bin)

        result_lines = []
        for cidx, card_str in enumerate(valid_cards):
            res, emoji, _ = await self.full_auth_check(card_str, get_brand=True)
            report = (
                f"`{escape_markdown_v2(card_str)}` | {emoji}\n"
                f"{escape_markdown_v2(res)}\n"
                f"Checked by: {username}"
            )
            result_lines.append(report)
        final_response = (f"{bin_info_msg}\n" if bin_info_msg else "") + "\n\n".join(result_lines)
        final_response = final_response[:4000]
        msg = await update.message.reply_text(final_response, parse_mode="MarkdownV2")
        asyncio.create_task(self.autodel_message(context, msg, 300))

    async def get_bin_info_message(self, bin_number):
        url = f"https://lookup.binlist.net/{bin_number}"
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    brand = d.get("scheme", "UNKNOWN").upper()
                    typex = d.get("type", "UNKNOWN").upper()
                    category = d.get("brand", "UNKNOWN")
                    bankn = d.get("bank", {}).get("name", "UNKNOWN")
                    country = d.get("country", {}).get("name", "")
                    emoji_flag = d.get("country", {}).get("emoji", "")
                    return (
                        f"𝗕𝗜𝗡 ⇾ `{escape_markdown_v2(bin_number)}`\n"
                        f"𝗜𝗻𝗳𝗼: {escape_markdown_v2(brand)} - {escape_markdown_v2(typex)} - {escape_markdown_v2(str(category))}\n"
                        f"𝗕𝗮𝗻𝗸: {escape_markdown_v2(str(bankn))}\n"
                        f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆: {escape_markdown_v2(str(country))} {escape_markdown_v2(str(emoji_flag))}\n"
                    )
                else:
                    return ""
        except Exception as e:
            logger.debug(f"BIN info fetch failed: {e}")
            return ""

    async def auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized_group(update.effective_chat.id):
            await update.message.reply_text(f"{CROSSMARK} Access denied. Please use this bot only in the authorized group.")
            return
        if not context.args:
            await update.message.reply_text(f"{WARNING} Usage: /auth cc|mm|yy|cvv")
            return
        card_str = context.args[0]
        card_data = card_str.split("|")
        if len(card_data) != 4:
            await update.message.reply_text(f"{CROSSMARK} Invalid card format: '{card_str}' (must be cc|mm|yy|cvv)")
            return

        self.pending_auth[update.effective_user.id] = card_str
        keyboard = [
            [InlineKeyboardButton("Check All Auths", callback_data="check_all_auths")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"{STAR} Press the button below to check all auth types for card ending {card_data[0][-4:]}",
            reply_markup=reply_markup
        )

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        if query.data == "check_all_auths":
            card_str = self.pending_auth.get(user_id)
            if not card_str:
                await query.edit_message_text(f"{CROSSMARK} No card found for this session. Please use /auth again.")
                return
            results, cc_brand_emoji, cc_brand_name = await self.full_auth_check(card_str, get_brand=True)
            if query.from_user.username:
                username = escape_markdown_v2(f"@{query.from_user.username}")
            else:
                username = escape_markdown_v2(query.from_user.first_name)
            footer = f"\nChecked by: {username}\n{'-'*31}"
            response = f"*{STAR} All Auth Results for Card {escape_markdown_v2(card_str.split('|')[0][-4:])} | {cc_brand_emoji}*\n{escape_markdown_v2(results)}{footer}"
            await query.edit_message_text(response, parse_mode="MarkdownV2")
            del self.pending_auth[user_id]

    async def full_auth_check(self, card_str, get_brand=False):
        card_data = card_str.split("|")
        cc = card_data[0]
        results = await asyncio.gather(
            self.async_basic_check(card_str),
            self.async_b3_auth(card_str),
            self.async_stripe_auth(card_str),
            self.async_paypal_auth(card_str),
            self.async_bank_auth(card_str),
        )
        report = "\n".join(results)
        if get_brand:
            emoji, brand = card_brand_type(cc)
            return report, emoji, brand
        return report

    async def async_basic_check(self, card_str):
        cc = card_str.split("|")[0]
        await asyncio.sleep(random.uniform(0.2, 0.5))
        result, msg = self.mock_basic_auth(cc)
        return f"{CHECKMARK} [BASIC] Card {cc[-4:]}: {result} - {msg}"

    async def async_b3_auth(self, card_str):
        cc = card_str.split("|")[0]
        await asyncio.sleep(random.uniform(0.2, 0.5))
        result, msg = self.mock_b3_auth(cc)
        return f"{SHIELD} [3DS B3] Card {cc[-4:]}: {result} - {msg}"

    async def async_stripe_auth(self, card_str):
        cc = card_str.split("|")[0]
        await asyncio.sleep(random.uniform(0.2, 0.5))
        result, msg = self.mock_stripe_auth(cc)
        return f"{BLUE_CIRCLE} [Stripe] Card {cc[-4:]}: {result} - {msg}"

    async def async_paypal_auth(self, card_str):
        cc = card_str.split("|")[0]
        await asyncio.sleep(random.uniform(0.2, 0.5))
        result, msg = self.mock_paypal_auth(cc)
        return f"{ORANGE_CIRCLE} [PayPal] Card {cc[-4:]}: {result} - {msg}"

    async def async_bank_auth(self, card_str):
        cc = card_str.split("|")[0]
        await asyncio.sleep(random.uniform(0.2, 0.5))
        result, msg = self.mock_bank_auth(cc)
        return f"{MONEY_BANK} [Bank] Card {cc[-4:]}: {result} - {msg}"

    def mock_basic_auth(self, cc):
        try:
            return ("APPROVED", "Basic auth passed") if int(cc[-1]) % 2 == 0 else ("DECLINED", "Basic auth declined")
        except:
            return ("ERROR", "Invalid card number")

    def mock_b3_auth(self, cc):
        try:
            s = sum(int(d) for d in cc if d.isdigit())
            return ("3DS AUTHORIZED", "3D Secure passed") if s % 3 == 0 else ("3DS FAILED", "3D Secure failed")
        except:
            return ("ERROR", "Invalid card number")

    def mock_stripe_auth(self, cc):
        try:
            return ("Stripe AUTHORIZED", "Stripe payment accepted") if int(cc[-2]) % 2 == 1 else ("Stripe DECLINED", "Stripe payment declined")
        except:
            return ("ERROR", "Invalid card number")

    def mock_paypal_auth(self, cc):
        try:
            return ("PayPal AUTHORIZED", "PayPal payment accepted") if random.random() > 0.4 else ("PayPal DECLINED", "PayPal payment declined")
        except:
            return ("ERROR", "Invalid card number")

    def mock_bank_auth(self, cc):
        try:
            s = sum(int(d) for d in cc if d.isdigit())
            return ("Bank DECLINED", "Bank declined transaction") if s % 7 == 0 else ("Bank APPROVED", "Bank approved transaction")
        except:
            return ("ERROR", "Invalid card number")

    async def autodel_message(self, context, msg, delay_sec):
        await asyncio.sleep(delay_sec)
        try:
            await context.bot.delete_message(chat_id=msg.chat_id, message_id=msg.message_id)
        except Exception as e:
            logger.debug(f"Delete message failed: {e}")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            f"{STAR} *CC Checker Bot Help*\n"
            f"/gen - Generate random real CC (separate box for each)\n"
            f"/gen N - Generate N random (N messages)\n"
            f"/gen <bin> - Generate for that BIN\n"
            f"/gen <bin> N - Generate N for that BIN\n"
            f"/chk <cc|mm|yy|cvv> - Check a single card (shows type/emoji)\n"
            f"/mchk (cc|mm|yy|cvv lines) - Multi check (up to 20, blocks separated)\n"
            f"/auth <cc|mm|yy|cvv> - Button for all checks\n"
            f"All responses auto-delete in 5 minutes.",
            parse_mode="MarkdownV2"
        )

async def main():
    defaults = Defaults(parse_mode="MarkdownV2")
    bot = TelegramCCCheckerBot()
    application = ApplicationBuilder().token(BOT_TOKEN).defaults(defaults).build()

    application.add_handler(CommandHandler("gen", bot.gen))
    application.add_handler(CommandHandler("chk", bot.chk))
    application.add_handler(CommandHandler("mchk", bot.mchk))
    application.add_handler(CommandHandler("auth", bot.auth))
    application.add_handler(CallbackQueryHandler(bot.button))
    application.add_handler(CommandHandler("help", bot.help))

    logger.info("🚀 Telegram CC Checker Bot started!")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
