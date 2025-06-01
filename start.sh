#!/bin/bash

# Optional: Activate your Python virtual environment if used
# source venv/bin/activate

# Set your required environment variables here
export BOT_TOKEN="7390661510:AAFgK-54qaOy31XPzNxb_MNn8gam_fWH38E"
export ADMIN_ID="5712886230" 
export GROUP_CHAT_ID="-1002433536975"

# Start the bot
echo "Starting Telegram CC Checker Bot..."
nohup python3 upcc.py > cc-bot.log 2>&1 &