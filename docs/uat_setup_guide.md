# UAT Setup Guide — AI Trader Bot

This guide walks you through setting up the AI Trader Bot from scratch on your own machine. You don't need any programming experience — just follow each step in order.

**What you'll end up with:** A fully automated Forex trading bot running on your computer that sends you Telegram updates on your phone.

**Time needed:** About 30-45 minutes.

**Cost:** Free. Everything uses free demo/trial accounts. No real money is involved.

---

## What You'll Need Before Starting

- A Windows 10 or 11 computer
- An internet connection
- A phone with Telegram installed

---

## Part 1 — Install Docker Desktop

Docker is what runs the bot. Think of it like a virtual machine that keeps everything contained and tidy.

1. Go to https://www.docker.com/products/docker-desktop/
2. Click **Download for Windows**
3. Run the installer — accept all the defaults
4. When it finishes, it will ask you to **restart your computer** — do that
5. After restart, Docker Desktop will open automatically. Wait until it says **"Docker Desktop is running"** in the bottom-left corner

**How to check it worked:** Open Command Prompt (search "cmd" in the Start menu) and type:
```
docker --version
```
You should see something like `Docker version 24.x.x`. If you get an error, restart Docker Desktop and try again.

---

## Part 2 — Install Git

Git is used to download the bot's code.

1. Go to https://git-scm.com/
2. Click **Download for Windows**
3. Run the installer — accept all the defaults (just keep clicking Next)

**How to check it worked:** Open a new Command Prompt and type:
```
git --version
```
You should see something like `git version 2.x.x`.

---

## Part 3 — Download the Bot

1. Open Command Prompt
2. Choose where you want the bot to live. For example, to put it on your Desktop:
   ```
   cd Desktop
   ```
3. Download the code:
   ```
   git clone https://github.com/joegooderham/ai-trader-bot.git
   ```
4. Go into the folder:
   ```
   cd ai-trader-bot
   ```

---

## Part 4 — Create Your IG Demo Account

IG is the broker — this is where the bot places trades. We're using a **free demo account** so no real money is involved.

1. Go to https://www.ig.com/uk
2. Click **Create account** (or **Open an account**)
3. Choose **Demo account**
4. Fill in your details and complete registration
5. Log in to your IG account
6. Go to **My IG** (top-right menu) then **Settings** then **API**
7. If you don't see an API section, you may need to apply for API access — follow the prompts on screen
8. Once approved, you'll see:
   - **API Key** — a long string of characters
   - **Username** — your IG login username
   - **Password** — your IG login password
   - **Account ID** — usually starts with a letter followed by numbers

**Write these four values down** — you'll need them in Part 7.

---

## Part 5 — Create Your Telegram Bot

This is how the bot sends you trade alerts and reports on your phone.

### Step 5a — Create the bot

1. Open Telegram on your phone
2. Search for **@BotFather** (it has a blue tick)
3. Tap **Start**
4. Send the message: `/newbot`
5. BotFather will ask you for a name — type something like `My Trading Bot`
6. BotFather will ask for a username — type something like `my_trading_123_bot` (must end in "bot")
7. BotFather will reply with your **Bot Token** — it looks like `7123456789:AAH1234abcd5678efgh`

**Copy this token** — you'll need it in Part 7.

### Step 5b — Get your Chat ID

1. Open a chat with your new bot in Telegram (search for the username you just created)
2. Send it any message, like `hello`
3. On your computer, open a browser and go to:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Replace `<YOUR_TOKEN>` with the token from step 7 above.
4. You'll see some text. Look for `"chat":{"id":` followed by a number like `123456789`

**Copy this number** — that's your Chat ID.

---

## Part 6 — Create Your Claude AI Account

Claude AI is what makes the bot intelligent — it analyses markets and explains its reasoning.

1. Go to https://console.anthropic.com
2. Sign up for a free account
3. Once logged in, click **API Keys** in the left menu
4. Click **Create Key**
5. Give it a name like `trading-bot`
6. Copy the key — it starts with `sk-ant-`

**Important:** You get free credits to start with. The bot uses very little — typically less than $1/month.

---

## Part 7 — Configure the Bot

This is where you plug in all the accounts you just created.

1. Make sure you're in the bot folder in Command Prompt:
   ```
   cd Desktop\ai-trader-bot
   ```

2. Create your config file:
   ```
   copy .env.example .env
   ```

3. Open the `.env` file in Notepad:
   ```
   notepad .env
   ```

4. Fill in your values (replace the placeholder text on each line):

   ```
   IG_API_KEY=paste_your_ig_api_key_here
   IG_USERNAME=your_ig_username
   IG_PASSWORD=your_ig_password
   IG_ACCOUNT_ID=your_ig_account_id
   IG_ENVIRONMENT=demo

   TELEGRAM_BOT_TOKEN=paste_your_bot_token_here
   TELEGRAM_CHAT_ID=paste_your_chat_id_here

   ANTHROPIC_API_KEY=paste_your_claude_key_here
   ```

5. **Leave all other values as they are** — the defaults are sensible
6. Save the file (Ctrl+S) and close Notepad

**Important:** The `IG_ENVIRONMENT` must say `demo`. This means no real money is used.

---

## Part 8 — Start the Bot

1. Make sure Docker Desktop is running (check for the whale icon in your taskbar)
2. In Command Prompt, make sure you're in the bot folder:
   ```
   cd Desktop\ai-trader-bot
   ```
3. Start the bot:
   ```
   docker-compose up -d
   ```
4. The first time, this will take a few minutes as it downloads and builds everything. You'll see progress bars and text scrolling — this is normal.
5. When it finishes, you should receive a **Telegram message** saying the bot has started.

If you don't get a Telegram message within 2 minutes, check the troubleshooting section below.

---

## Part 9 — Verify Everything Works

### Check the bot is running

In Command Prompt:
```
docker-compose ps
```

You should see three services, all showing `Up`:
```
ai-trader-bot      ... Up
ai-trader-mcp      ... Up
ai-trader-health   ... Up
```

### Check the logs

```
docker-compose logs -f forex-bot
```

You should see log messages about scanning pairs and checking prices. Press `Ctrl+C` to stop watching.

### Test the Telegram commands

Open your Telegram bot chat and try these commands:

| Command | What it does |
|---------|-------------|
| `/help` | Shows all available commands |
| `/health` | Checks if all systems are working |
| `/positions` | Shows any open trades |
| `/today` | Shows today's trading activity |
| `/fallbacktest` | Tests the backup data source |

---

## What Happens Next

The bot is now running and will:

- **Scan markets every 15 minutes** looking for trade opportunities
- **Send you a Telegram alert** every time it opens or closes a trade
- **Send a daily report** after 23:59 UTC each night
- **Send a weekly report** every Sunday evening
- **Alert you immediately** if anything goes wrong

**Markets are only open Monday to Friday.** The bot won't find trades over the weekend, but it will still be running and monitoring.

---

## Stopping the Bot

If you want to stop the bot at any time:
```
docker-compose down
```

This safely closes all positions first. To start it again:
```
docker-compose up -d
```

---

## Updating the Bot

If you're told a new version is available:
```
git pull
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

---

## Troubleshooting

### "I didn't get a Telegram message"

1. Check Docker is running: `docker-compose ps` — all three services should show `Up`
2. Check the logs for errors: `docker-compose logs forex-bot`
3. Make sure you sent a message to your bot in Telegram before starting (BotFather step)
4. Double-check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in your `.env` file
5. Restart: `docker-compose down` then `docker-compose up -d`

### "IG connection error" or "403 error"

- Check `IG_API_KEY`, `IG_USERNAME`, `IG_PASSWORD` are correct in `.env`
- Make sure `IG_ENVIRONMENT=demo` (not live)
- IG demo accounts have a data limit — the bot handles this automatically by switching to Yahoo Finance as a backup. You'll get a Telegram alert if this happens.

### "Bot started but no trades are happening"

This is normal. The bot only trades when it finds a signal with 60%+ confidence. During quiet market periods (evenings, weekends), it may scan for hours without trading. Check the logs to confirm it's still scanning:
```
docker-compose logs -f forex-bot
```

### "Container keeps restarting"

Check the logs to see what's wrong:
```
docker-compose logs forex-bot
```

The most common cause is a missing or incorrect value in `.env`.

### "Out of disk space"

Clean up old Docker data:
```
docker system prune
```

---

## Important Notes

- This bot uses a **demo account** — no real money is at risk
- Forex trading involves substantial risk — do not switch to a live account until you understand the risks
- The bot runs 24/7 as long as Docker Desktop is running on your computer
- If your computer goes to sleep or shuts down, the bot will stop — it restarts automatically when Docker starts back up
- Your trade history is saved in the `data/` folder
- Your secret keys are in the `.env` file — never share this file with anyone
