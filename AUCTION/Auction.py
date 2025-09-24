import logging
import random
import csv
import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

# Enhanced logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler('auction_bot.log'),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Database file
DB_FILE = "enhanced_auction_data_v15.json"

# Enhanced auction state with more detailed tracking
auction_data = {
    "is_auction_live": False,
    "owner_id": None,
    "current_player": None,
    "current_bid": 0.0,
    "highest_bidder_id": None,
    "highest_bidder_name": None,
    "teams": {},
    "players": [],
    "available_players": [],
    "sold_players": [],
    "unsold_players": [],
    "auction_history": [],
    "admin_ids": [],
    "bidding_timer": None,
    "timer_reminder_sent": False,
    "main_chat_id": None,
    "auction_stats": {
        "total_auctions": 0,
        "total_amount_spent": 0,
        "highest_bid": 0,
        "most_expensive_player": None,
        "average_price": 0
    },
    "settings": {
        "timer_duration": 30,
        "reminder_time": 3,
        "auto_next": True,
        "show_player_photos": True,
        "bidding_increment": 0.1
    }
}

# ====================================================================================================
# Enhanced Utility Functions
# ====================================================================================================

def load_data():
    """Enhanced data loading with error handling and data migration."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            # Migrate and update auction data
            auction_data.update(data)
            
            # Convert IDs back to integers
            if auction_data.get("owner_id"):
                auction_data["owner_id"] = int(auction_data["owner_id"])
            
            auction_data["admin_ids"] = [int(id) for id in data.get("admin_ids", [])]
            
            if auction_data.get("main_chat_id"):
                auction_data["main_chat_id"] = int(auction_data["main_chat_id"])
            
            # Convert team captain IDs
            for team in auction_data["teams"].values():
                if team.get("captain_id"):
                    team["captain_id"] = int(team["captain_id"])
            
            # Ensure all required fields exist
            if "auction_stats" not in auction_data:
                auction_data["auction_stats"] = {
                    "total_auctions": 0,
                    "total_amount_spent": 0,
                    "highest_bid": 0,
                    "most_expensive_player": None,
                    "average_price": 0
                }
            
            if "settings" not in auction_data:
                auction_data["settings"] = {
                    "timer_duration": 30,
                    "reminder_time": 3,
                    "auto_next": True,
                    "show_player_photos": True,
                    "bidding_increment": 0.1
                }
                
            logger.info("Enhanced auction state loaded successfully.")
            
        except Exception as e:
            logger.error(f"Failed to load data from {DB_FILE}: {e}")
    else:
        logger.info("No state file found. Starting with enhanced clean state.")

def save_data():
    """Enhanced data saving with backup."""
    if os.path.exists(DB_FILE):
        backup_file = f"{DB_FILE}.backup"
        try:
            os.rename(DB_FILE, backup_file)
        except:
            pass
    
    data_to_save = {
        **auction_data,
        "owner_id": str(auction_data["owner_id"]) if auction_data["owner_id"] else None,
        "admin_ids": [str(id) for id in auction_data["admin_ids"]],
        "teams": {
            k: {
                **v,
                "captain_id": str(v["captain_id"]) if v.get("captain_id") else None,
                "original_purse": v.get("original_purse", v.get("purse", 1000))
            } for k, v in auction_data["teams"].items()
        },
        "main_chat_id": str(auction_data["main_chat_id"]) if auction_data["main_chat_id"] else None,
    }
    
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4, ensure_ascii=False)
        logger.info("Enhanced auction state saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save data to {DB_FILE}: {e}")

async def get_user_profile(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    """Fetch detailed user profile from Telegram."""
    try:
        user_info = await context.bot.get_chat(user_id)
        profile = {
            "id": user_id,
            "username": user_info.username,
            "first_name": user_info.first_name,
            "last_name": user_info.last_name,
            "full_name": f"{user_info.first_name or ''} {user_info.last_name or ''}".strip(),
            "profile_photo": None
        }
        
        if user_info.photo:
            profile["profile_photo"] = user_info.photo.big_file_id
            
        return profile
    except Exception as e:
        logger.error(f"Failed to fetch user profile for {user_id}: {e}")
        return {
            "id": user_id,
            "username": None,
            "first_name": "Unknown",
            "last_name": "",
            "full_name": "Unknown User",
            "profile_photo": None
        }

def calculate_team_stats(team_data: Dict) -> Dict[str, Any]:
    """Calculate comprehensive team statistics."""
    players = team_data.get("players", [])
    if not players:
        return {
            "total_players": 0,
            "total_spent": 0,
            "average_price": 0,
            "most_expensive": None,
            "role_distribution": {},
            "remaining_purse": team_data.get("purse", 0)
        }
    
    total_spent = sum(float(p.get("purchase_price", 0)) for p in players)
    role_counts = {}
    most_expensive = max(players, key=lambda p: float(p.get("purchase_price", 0)), default=None)
    
    for player in players:
        role = player.get("role", "Unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    
    return {
        "total_players": len(players),
        "total_spent": total_spent,
        "average_price": total_spent / len(players) if players else 0,
        "most_expensive": most_expensive,
        "role_distribution": role_counts,
        "remaining_purse": team_data.get("purse", 0)
    }

# ====================================================================================================
# Enhanced Timer Functions
# ====================================================================================================

async def send_timer_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Enhanced timer reminder with countdown."""
    if auction_data["is_auction_live"] and not auction_data["timer_reminder_sent"]:
        auction_data["timer_reminder_sent"] = True
        
        player = auction_data["current_player"]
        current_bid = auction_data["current_bid"]
        bidder = auction_data["highest_bidder_name"] or "No one"
        
        reminder_text = (
            f"⚡ <b>FINAL CALL!</b> ⚡\n\n"
            f"👤 Player: <b>{player['name']}</b> ({player.get('role', 'Player')})\n"
            f"💰 Current Bid: <b>₹{current_bid}</b> by <b>{bidder}</b>\n\n"
            f"🚨 <b>{auction_data['settings']['reminder_time']} SECONDS LEFT!</b> 🚨\n"
            f"Last chance to bid! 💸"
        )
        
        await context.bot.send_message(
            chat_id=auction_data["main_chat_id"],
            text=reminder_text,
            parse_mode="HTML"
        )

async def start_timer(context: ContextTypes.DEFAULT_TYPE, duration: int):
    """Enhanced timer with dynamic duration."""
    auction_data["bidding_timer"] = datetime.now() + timedelta(seconds=duration)
    auction_data["timer_reminder_sent"] = False
    
    if duration > auction_data["settings"]["reminder_time"]:
        context.job_queue.run_once(
            send_timer_reminder,
            duration - auction_data["settings"]["reminder_time"],
            chat_id=auction_data.get("main_chat_id"),
            name="timer_reminder"
        )
    
    context.job_queue.run_once(
        end_bid_automatically,
        duration,
        chat_id=auction_data.get("main_chat_id"),
        name="end_auction"
    )
    
    logger.info(f"Enhanced timer started for {duration} seconds.")

async def end_bid_automatically(context: ContextTypes.DEFAULT_TYPE):
    """Auto-end auction with enhanced conclusion."""
    if auction_data["is_auction_live"]:
        await conclude_auction(context.bot, auction_data["main_chat_id"], context)

def reset_timer():
    """Enhanced timer reset with job cleanup."""
    jobs_to_remove = ["timer_reminder", "end_auction"]
    for name in jobs_to_remove:
        try:
            current_jobs = application.job_queue.get_jobs_by_name(name)
            for job in current_jobs:
                job.schedule_removal()
        except:
            pass
    
    auction_data["bidding_timer"] = None
    auction_data["timer_reminder_sent"] = False
    logger.info("Enhanced timer reset completed.")

# ====================================================================================================
# Enhanced Command Handlers
# ====================================================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced welcome message with interactive setup."""
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "🏏 <b>Enhanced Cricket Auction Bot</b> 🏏\n\n"
            "This bot is designed for group chats! Please add me to a group to start an amazing auction experience! 🎉\n\n"
            "✨ <b>New Features:</b>\n"
            "• Rich player profiles with photos 📸\n"
            "• Advanced team analytics 📊\n"
            "• Real-time bidding insights ⚡\n"
            "• Enhanced auction management ⚙️\n\n"
            "Use <code>/help</code> in the group for a complete guide! 📚",
            parse_mode="HTML"
        )
        return

    auction_data["main_chat_id"] = update.effective_chat.id
    
    keyboard = [
        [InlineKeyboardButton("📚 Quick Setup Guide", callback_data="setup_guide")],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data="bot_settings")],
        [InlineKeyboardButton("📊 Auction Stats", callback_data="auction_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        "🏏 <b>Enhanced Cricket Auction Bot v15.0</b> 🏆\n\n"
        "🌟 <b>Welcome to the Ultimate Auction Experience!</b> 🌟\n\n"
        "Get ready for a thrilling cricket auction with advanced features! 🚀\n"
        "Use <code>/help</code> to explore all commands and start building your dream team! 🏟️\n\n"
        "<b>Quick Start:</b>\n"
        "1. Set the owner with <code>/set_owner</code> 👑\n"
        "2. Register teams with <code>/register</code> 💼\n"
        "3. Load players with <code>/load_players</code> 📋\n"
        "4. Start bidding with <code>/start_auction</code> 🎬\n\n"
        "Click below to begin or check settings! ⚙️"
    )
    
    await update.message.reply_text(message, parse_mode="HTML", reply_markup=reply_markup)
    save_data()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced help command with detailed, user-friendly guidance."""
    message = (
        "📜 <b>Cricket Auction Bot v15.0 Guide</b> 📜\n\n"
        "Welcome to the ultimate cricket auction platform! 🌟 This bot offers a dynamic auction experience with rich player profiles, advanced analytics, and real-time bidding. Here's your complete guide to dominating the auction! 🏆\n\n"
        "👑 <b>Owner Commands</b> (Exclusive to Bot Owner)\n"
        "• <code>/set_owner</code> – Be the first to claim bot ownership. Only one owner allowed! 🔒\n"
        "• <code>/add_admin</code> – Reply to a user to grant them admin privileges. 🛡️\n"
        "• <code>/settings [setting] [value]</code> – Configure bot settings (e.g., timer duration, bid increments). ⚙️\n\n"
        "🛡️ <b>Admin Commands</b> (For Admins Only)\n"
        "• <code>/register &lt;TeamName&gt; &lt;Purse&gt;</code> – Register a new team (e.g., <code>/register SuperKings 1000</code>). 💼\n"
        "• <code>/set_captain &lt;TeamName&gt;</code> – Reply to a user to assign them as a team captain. 🧑‍✈️\n"
        "• <code>/load_players &lt;file.csv&gt;</code> – Load players from a CSV file (columns: name, role, base_price, id, username, rating, speciality). 📋\n"
        "• <code>/start_auction</code> – Launch an auction for a random player. 🎬\n"
        "• <code>/end_auction</code> – Manually conclude the current auction. 🏁\n"
        "• <code>/skip</code> – Skip the current player without a sale. ⏭️\n"
        "• <code>/reset_auction</code> – Reset the entire auction (clears teams, players, and stats). 🔄\n\n"
        "🏏 <b>Captain & Public Commands</b> (Available to All)\n"
        "• <code>/bid &lt;amount&gt;</code> – Place a bid for the current player (e.g., <code>/bid 15.5</code>). 💰\n"
        "• <code>/quick_bid</code> – Access quick bid options for faster bidding. 🚀\n"
        "• <code>/my_team</code> – View your team's squad and purse (captains only). 🏟️\n"
        "• <code>/team_stats</code> – Detailed analytics for your team (captains only). 📊\n"
        "• <code>/teams</code> – List all teams, their purses, and players. 📋\n"
        "• <code>/player_info</code> – View details of the current player up for auction. 👤\n"
        "• <code>/auction_stats</code> – Check overall auction statistics and insights. 📈\n"
        "• <code>/status</code> – See the current auction status (player, bid, time left). 🔍\n"
        "• <code>/history</code> – Review the auction history of sold/unsold players. 📜\n"
        "• <code>/leaderboard</code> – Check team rankings by players and purse. 🏆\n\n"
        "💡 <b>Tips for Success</b>\n"
        "- Bidding is done in the group chat using <code>/bid</code> or quick bid buttons. 💸\n"
        "- Admins can customize settings like timer duration and bid increments. ⚙️\n"
        "- Use <code>/player_info</code> to strategize your bids based on player stats! 🎯\n"
        "- Check <code>/auction_stats</code> for insights to stay ahead! 📊\n\n"
        "📋 <b>CSV Format</b>: name, role, base_price, id, username, rating, speciality\n"
        "🚀 Ready to build your dream team? Start with <code>/set_owner</code>! 🏏"
    )
    await update.message.reply_text(message, parse_mode="HTML")

async def set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets the first user to use this command as the bot's owner."""
    user_id = update.effective_user.id
    if auction_data["owner_id"] is None:
        auction_data["owner_id"] = user_id
        if user_id not in auction_data["admin_ids"]:
            auction_data["admin_ids"].append(user_id)
        await update.message.reply_text(
            "👑 <b>Congratulations!</b> You are now the <b>Bot Owner</b> with full control! 🎉",
            parse_mode="HTML"
        )
        save_data()
    else:
        await update.message.reply_text(
            "🔒 <b>Oops!</b> The bot already has an owner. Only they can add admins. 🛡️",
            parse_mode="HTML"
        )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Adds a new admin. Owner-only command."""
    user_id = update.effective_user.id
    if user_id != auction_data["owner_id"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only the bot owner can add admins. 🔒",
            parse_mode="HTML"
        )
        return

    try:
        new_admin_user = update.message.reply_to_message.from_user
        new_admin_id = new_admin_user.id
        if new_admin_id not in auction_data["admin_ids"]:
            auction_data["admin_ids"].append(new_admin_id)
            await update.message.reply_text(
                f"✅ <b>Success!</b> @{new_admin_user.username} is now an admin! 🛡️",
                parse_mode="HTML"
            )
            save_data()
        else:
            await update.message.reply_text(
                f"❕ @{new_admin_user.username} is already an admin! 😊",
                parse_mode="HTML"
            )
    except (IndexError, AttributeError):
        await update.message.reply_text(
            "❌ <b>Error!</b> Please reply to the user you want to make an admin. 🙌",
            parse_mode="HTML"
        )

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registers a new team. Admin-only command."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can register teams. 🛡️",
            parse_mode="HTML"
        )
        return

    try:
        team_name = context.args[0]
        purse = float(context.args[1])
        if purse <= 0:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text(
            "❌ <b>Invalid Input!</b> Use <code>/register &lt;TeamName&gt; &lt;PurseAmount&gt;</code> (e.g., <code>/register SuperKings 1000</code>). 💼",
            parse_mode="HTML"
        )
        return

    if team_name in auction_data["teams"]:
        await update.message.reply_text(
            f"❕ <b>Oops!</b> Team '{team_name}' already exists! Try a different name. 😊",
            parse_mode="HTML"
        )
        return

    auction_data["teams"][team_name] = {
        "purse": purse,
        "original_purse": purse,
        "players": [],
        "captain_id": None
    }
    await update.message.reply_text(
        f"✅ <b>Team Registered!</b> '{team_name}' is ready with a purse of ₹{purse}! 🎉 Use <code>/set_captain {team_name}</code> while replying to a user to assign a captain. 🧑‍✈️",
        parse_mode="HTML"
    )
    save_data()
    logger.info(f"Team {team_name} registered.")

async def set_captain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets the captain for a team. Admin-only command."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can set captains. 🛡️",
            parse_mode="HTML"
        )
        return

    try:
        team_name = context.args[0]
        captain_user = update.message.reply_to_message.from_user
        captain_user_id = captain_user.id
        captain_username = captain_user.username or "User"
    except (IndexError, AttributeError):
        await update.message.reply_text(
            "❌ <b>Invalid Input!</b> Use <code>/set_captain &lt;TeamName&gt;</code> while replying to the user you want as captain. 🧑‍✈️",
            parse_mode="HTML"
        )
        return

    team_data = auction_data["teams"].get(team_name)
    if not team_data:
        await update.message.reply_text(
            f"❌ <b>Error!</b> Team '{team_name}' not found. 😕",
            parse_mode="HTML"
        )
        return

    for team in auction_data["teams"].values():
        if team.get("captain_id") == captain_user_id:
            await update.message.reply_text(
                f"❕ <b>Oops!</b> This user is already a captain for another team. 🧑‍✈️",
                parse_mode="HTML"
            )
            return

    team_data["captain_id"] = captain_user_id
    await update.message.reply_text(
        f"✅ <b>Captain Assigned!</b> @{captain_username} is now the captain of <b>{team_name}</b>! 🏟️",
        parse_mode="HTML"
    )
    save_data()
    logger.info(f"Captain for {team_name} set to {captain_user_id}")

async def load_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loads players from a CSV file. Admin-only command."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can load players. 🛡️",
            parse_mode="HTML"
        )
        return

    try:
        if not context.args:
            await update.message.reply_text(
                "❌ <b>Invalid Input!</b> Provide a file name, e.g., <code>/load_players players.csv</code>. 📋",
                parse_mode="HTML"
            )
            return
        file_path = context.args[0]
        with open(file_path, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            expected_fields = ["name", "role", "base_price", "id", "username", "rating", "speciality"]
            if not all(field in reader.fieldnames for field in ["name", "role", "base_price"]):
                await update.message.reply_text(
                    "❌ <b>Invalid CSV!</b> File must contain 'name', 'role', and 'base_price' columns. 📋",
                    parse_mode="HTML"
                )
                return
            auction_data["players"] = [row for row in reader]
            auction_data["available_players"] = list(auction_data["players"])
        await update.message.reply_text(
            f"✅ <b>Success!</b> Loaded {len(auction_data['players'])} players from '{file_path}'! 📋",
            parse_mode="HTML"
        )
        save_data()
        logger.info(f"Players loaded from {file_path}")
    except FileNotFoundError:
        await update.message.reply_text(
            f"❌ <b>Error!</b> File '{file_path}' not found. 😕",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error!</b> Failed to load CSV: {e} 😕",
            parse_mode="HTML"
        )

async def start_auction_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually starts the auction for a random player. Admin-only command."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can start the auction. 🛡️",
            parse_mode="HTML"
        )
        return
    await start_auction(context)

async def start_auction(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced auction start with rich player profiles."""
    if auction_data["is_auction_live"]:
        return

    if not auction_data["available_players"]:
        await context.bot.send_message(
            chat_id=auction_data["main_chat_id"],
            text="🏁 <b>Auction Complete!</b> All players have been auctioned! 🎉\n\n"
                 "Use <code>/auction_stats</code> to see final results! 📊",
            parse_mode="HTML"
        )
        return

    player_to_auction = random.choice(auction_data["available_players"])
    auction_data["is_auction_live"] = True
    auction_data["current_player"] = player_to_auction
    
    base_price = float(player_to_auction.get("base_price", 0))
    auction_data["current_bid"] = base_price
    auction_data["highest_bidder_id"] = None
    auction_data["highest_bidder_name"] = None
    
    user_profile = None
    if player_to_auction.get('id'):
        try:
            user_profile = await get_user_profile(context, int(player_to_auction['id']))
        except:
            pass
    
    player_name = user_profile["full_name"] if user_profile else player_to_auction.get('name', 'Unknown')
    username = user_profile["username"] if user_profile else player_to_auction.get('username', 'N/A')
    
    caption = (
        f"🔥 <b>NEW AUCTION ALERT!</b> 🔥\n\n"
        f"👤 <b>Player:</b> {player_name}\n"
        f"📱 <b>Username:</b> @{username}\n"
        f"🏏 <b>Role:</b> {player_to_auction.get('role', 'All-rounder')}\n"
        f"💰 <b>Base Price:</b> ₹{base_price}\n"
        f"⭐ <b>Rating:</b> {player_to_auction.get('rating', 'N/A')}/10\n"
        f"🎯 <b>Speciality:</b> {player_to_auction.get('speciality', 'Versatile Player')}\n\n"
        f"🚀 <b>Captains, start your bidding!</b>\n"
        f"Use <code>/bid &lt;amount&gt;</code> or <code>/quick_bid</code> 💸"
    )
    
    keyboard = [
        [
            InlineKeyboardButton(f"₹{base_price + 5}", callback_data=f"quick_bid_{base_price + 5}"),
            InlineKeyboardButton(f"₹{base_price + 10}", callback_data=f"quick_bid_{base_price + 10}"),
            InlineKeyboardButton(f"₹{base_price + 20}", callback_data=f"quick_bid_{base_price + 20}")
        ],
        [InlineKeyboardButton("👤 Player Profile", callback_data="player_profile")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if auction_data["settings"]["show_player_photos"] and user_profile and user_profile["profile_photo"]:
            await context.bot.send_photo(
                chat_id=auction_data["main_chat_id"],
                photo=user_profile["profile_photo"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            raise Exception("No photo or photos disabled")
    except:
        await context.bot.send_message(
            chat_id=auction_data["main_chat_id"],
            text=caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    await start_timer(context, auction_data["settings"]["timer_duration"])
    
    auction_data["auction_stats"]["total_auctions"] += 1
    save_data()
    
    logger.info(f"Enhanced auction started for {player_name}")

async def enhanced_bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced bidding with better validation and feedback."""
    user_id = update.effective_user.id
    
    if update.effective_chat.type not in ["group", "supergroup"]:
        await update.message.reply_text(
            "❌ <b>Bids in Group Only!</b> Place your bids in the group chat! 🏟️",
            parse_mode="HTML"
        )
        return

    team_data = None
    team_name = None
    for name, data in auction_data["teams"].items():
        if data.get("captain_id") == user_id:
            team_data = data
            team_name = name
            break
    
    if not team_data:
        await update.message.reply_text(
            "🚫 <b>Not Authorized!</b> You need to be a team captain to bid. 🧑‍✈️",
            parse_mode="HTML"
        )
        return

    if not auction_data["is_auction_live"]:
        await update.message.reply_text(
            "❌ <b>No Active Auction!</b> Wait for the next player. 😊",
            parse_mode="HTML"
        )
        return

    try:
        bid_amount = float(context.args[0])
        min_increment = auction_data["settings"]["bidding_increment"]
        if bid_amount < auction_data["current_bid"] + min_increment:
            await update.message.reply_text(
                f"❌ <b>Bid Too Low!</b>\n"
                f"Current: ₹{auction_data['current_bid']}\n"
                f"Minimum: ₹{auction_data['current_bid'] + min_increment} 💰",
                parse_mode="HTML"
            )
            return
    except (IndexError, ValueError):
        await update.message.reply_text(
            "❌ <b>Invalid Bid!</b> Use <code>/bid &lt;amount&gt;</code> (e.g., <code>/bid 15.5</code>) 💰",
            parse_mode="HTML"
        )
        return

    if team_data["purse"] < bid_amount:
        await update.message.reply_text(
            f"❌ <b>Insufficient Funds!</b>\n"
            f"Your purse: ₹{team_data['purse']}\n"
            f"Required: ₹{bid_amount} 😕",
            parse_mode="HTML"
        )
        return
    
    previous_bidder = auction_data["highest_bidder_name"]
    auction_data["current_bid"] = bid_amount
    auction_data["highest_bidder_id"] = user_id
    auction_data["highest_bidder_name"] = team_name
    
    player = auction_data["current_player"]
    remaining_purse = team_data["purse"] - bid_amount
    
    bid_text = (
        f"🔥 <b>NEW BID ALERT!</b> 🔥\n\n"
        f"💰 <b>{team_name}</b> bids <b>₹{bid_amount}</b>\n"
        f"👤 Player: <b>{player['name']}</b> ({player.get('role', 'Player')})\n"
        f"💵 Remaining purse: <b>₹{remaining_purse:.1f}</b>\n\n"
    )
    
    if previous_bidder and previous_bidder != team_name:
        bid_text += f"🏃‍♂️ <b>{previous_bidder}</b> has been outbid!\n\n"
    
    if auction_data["bidding_timer"]:
        time_left = auction_data["bidding_timer"] - datetime.now()
        seconds_left = max(0, int(time_left.total_seconds()))
        bid_text += f"⏰ Time remaining: <b>{seconds_left}s</b>"
    
    await update.message.reply_text(bid_text, parse_mode="HTML")
    
    reset_timer()
    await start_timer(context, auction_data["settings"]["timer_duration"])
    save_data()
    
    logger.info(f"Enhanced bid: {bid_amount} by {team_name} for {player['name']}")

async def quick_bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick bid interface for captains."""
    user_id = update.effective_user.id
    
    team_name = None
    for name, data in auction_data["teams"].items():
        if data.get("captain_id") == user_id:
            team_name = name
            break
    
    if not team_name:
        await update.message.reply_text(
            "🚫 <b>Not a Captain!</b> Only team captains can use quick bid! 🧑‍✈️",
            parse_mode="HTML"
        )
        return
    
    if not auction_data["is_auction_live"]:
        await update.message.reply_text(
            "❌ <b>No Active Auction!</b> Wait for the next player. 😊",
            parse_mode="HTML"
        )
        return
    
    current_bid = auction_data["current_bid"]
    increment = auction_data["settings"]["bidding_increment"]
    
    keyboard = [
        [
            InlineKeyboardButton(f"₹{current_bid + increment}", callback_data=f"quick_bid_{current_bid + increment}"),
            InlineKeyboardButton(f"₹{current_bid + increment*2}", callback_data=f"quick_bid_{current_bid + increment*2}"),
            InlineKeyboardButton(f"₹{current_bid + increment*5}", callback_data=f"quick_bid_{current_bid + increment*5}")
        ],
        [
            InlineKeyboardButton(f"₹{current_bid + 10}", callback_data=f"quick_bid_{current_bid + 10}"),
            InlineKeyboardButton(f"₹{current_bid + 25}", callback_data=f"quick_bid_{current_bid + 25}"),
            InlineKeyboardButton(f"₹{current_bid + 50}", callback_data=f"quick_bid_{current_bid + 50}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🚀 <b>Quick Bid Options</b> 🚀\n\n"
        f"Current bid: <b>₹{current_bid}</b>\n"
        f"Choose your bid below: 💸",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def end_auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually ends the current auction. Admin-only."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can end the auction. 🛡️",
            parse_mode="HTML"
        )
        return
    if not auction_data["is_auction_live"]:
        await update.message.reply_text(
            "❌ <b>No Active Auction!</b> Start one with <code>/start_auction</code>. 😊",
            parse_mode="HTML"
        )
        return
    
    await conclude_auction(context.bot, auction_data["main_chat_id"], context)

async def conclude_auction(bot, chat_id, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced auction conclusion with detailed results."""
    reset_timer()
    
    player = auction_data["current_player"]
    bid = auction_data["current_bid"]
    bidder_id = auction_data["highest_bidder_id"]
    bidder_name = auction_data["highest_bidder_name"]

    if bidder_id:
        team_data = next(
            (t for t in auction_data["teams"].values() if t.get("captain_id") == bidder_id),
            None
        )
        
        if not team_data or team_data["purse"] < bid:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Bid Invalid!</b> {bidder_name} has insufficient funds! 😕",
                parse_mode="HTML"
            )
            return

        enhanced_player = {
            **player,
            "purchase_price": bid,
            "purchased_by": bidder_name,
            "purchase_date": datetime.now().isoformat(),
            "auction_number": auction_data["auction_stats"]["total_auctions"]
        }
        
        team_data["players"].append(enhanced_player)
        team_data["purse"] = round(team_data["purse"] - bid, 2)
        auction_data["available_players"].remove(player)
        auction_data["sold_players"].append(enhanced_player)
        
        auction_data["auction_history"].append({
            "player": player["name"],
            "price": bid,
            "team": bidder_name,
            "status": "Sold",
            "timestamp": datetime.now().isoformat()
        })
        
        auction_data["auction_stats"]["total_amount_spent"] += bid
        if bid > auction_data["auction_stats"]["highest_bid"]:
            auction_data["auction_stats"]["highest_bid"] = bid
            auction_data["auction_stats"]["most_expensive_player"] = enhanced_player
        
        sold_count = len(auction_data["sold_players"])
        if sold_count > 0:
            auction_data["auction_stats"]["average_price"] = (
                auction_data["auction_stats"]["total_amount_spent"] / sold_count
            )
        
        team_stats = calculate_team_stats(team_data)
        
        sold_message = (
            f"🎉 <b>SOLD!</b> 🎉\n\n"
            f"👤 <b>{player['name']}</b> ({player.get('role', 'Player')})\n"
            f"💰 Final Price: <b>₹{bid}</b>\n"
            f"🏆 Winner: <b>{bidder_name}</b>\n\n"
            f"📊 <b>Team Update:</b>\n"
            f"• Players: {team_stats['total_players']}\n"
            f"• Total Spent: ₹{team_stats['total_spent']:.1f}\n"
            f"• Remaining: ₹{team_stats['remaining_purse']:.1f}\n\n"
            f"🔥 <b>Fantastic addition to {bidder_name}!</b> 💪"
        )
        
        await bot.send_message(chat_id=chat_id, text=sold_message, parse_mode="HTML")
        logger.info(f"Player {player['name']} sold to {bidder_name} for ₹{bid}")

    else:
        auction_data["available_players"].remove(player)
        auction_data["unsold_players"].append(player)
        auction_data["auction_history"].append({
            "player": player["name"],
            "price": 0,
            "team": "N/A",
            "status": "Unsold",
            "timestamp": datetime.now().isoformat()
        })
        
        unsold_message = (
            f"😔 <b>UNSOLD</b> 😔\n\n"
            f"👤 <b>{player['name']}</b> ({player.get('role', 'Player')})\n"
            f"💰 Base Price: ₹{player.get('base_price', 0)}\n\n"
            f"No team showed interest at the base price.\n"
            f"Player will be available in future rounds! 🔄"
        )
        
        await bot.send_message(chat_id=chat_id, text=unsold_message, parse_mode="HTML")
        logger.info(f"Player {player['name']} went unsold")

    auction_data["is_auction_live"] = False
    auction_data["current_player"] = None
    auction_data["current_bid"] = 0.0
    auction_data["highest_bidder_id"] = None
    auction_data["highest_bidder_name"] = None
    save_data()
    export_enhanced_results()
    
    if auction_data["settings"]["auto_next"] and auction_data["available_players"]:
        await asyncio.sleep(3)
        await start_auction(context)
    elif not auction_data["available_players"]:
        await show_final_results(bot, chat_id)

async def skip_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skips the current player without a sale. Admin-only."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can skip players. 🛡️",
            parse_mode="HTML"
        )
        return
    if not auction_data["is_auction_live"]:
        await update.message.reply_text(
            "❌ <b>No Active Auction!</b> Start one with <code>/start_auction</code>. 😊",
            parse_mode="HTML"
        )
        return

    player = auction_data["current_player"]
    auction_data["auction_history"].append({
        "player": player["name"],
        "price": 0,
        "team": "N/A",
        "status": "Skipped",
        "timestamp": datetime.now().isoformat()
    })
    auction_data["unsold_players"].append(player)
    auction_data["available_players"].remove(player)
    
    await context.bot.send_message(
        chat_id=auction_data["main_chat_id"],
        text=f"⏭️ <b>Skipped!</b> <b>{player['name']}</b> ({player.get('role', 'Player')}) has been skipped. 😕",
        parse_mode="HTML"
    )
    
    auction_data["is_auction_live"] = False
    auction_data["current_player"] = None
    auction_data["current_bid"] = 0.0
    auction_data["highest_bidder_id"] = None
    auction_data["highest_bidder_name"] = None
    reset_timer()
    save_data()
    export_enhanced_results()

    if auction_data["settings"]["auto_next"] and auction_data["available_players"]:
        await asyncio.sleep(3)
        await start_auction(context)
    else:
        await show_final_results(context.bot, auction_data["main_chat_id"])

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the current auction status."""
    if auction_data["is_auction_live"]:
        player = auction_data["current_player"]
        bid = auction_data["current_bid"]
        bidder = auction_data["highest_bidder_name"] or "No one"
        
        time_left = auction_data["bidding_timer"] - datetime.now()
        seconds_left = max(0, int(time_left.total_seconds()))

        user_profile = None
        if player.get('id'):
            try:
                user_profile = await get_user_profile(context, int(player['id']))
            except:
                pass
        player_name = user_profile["full_name"] if user_profile else player.get('name', 'Unknown')
        
        message = (
            f"📊 <b>Current Auction Status</b> 📊\n\n"
            f"👤 Player: <b>{player_name}</b> ({player.get('role', 'Player')})\n"
            f"💰 Base Price: <b>₹{player['base_price']}</b>\n"
            f"💸 Current Bid: <b>₹{bid}</b>\n"
            f"👑 Highest Bidder: <b>{bidder}</b>\n"
            f"⏰ Time Remaining: <b>{seconds_left} seconds</b>"
        )
    else:
        message = "😴 <b>No Active Auction!</b> Admins can start one with <code>/start_auction</code>. 🚀"

    await update.message.reply_text(message, parse_mode="HTML")

async def my_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's team information. Captains only."""
    user_id = update.effective_user.id
    team_data = None
    team_name = None
    for name, data in auction_data["teams"].items():
        if data.get("captain_id") == user_id:
            team_data = data
            team_name = name
            break
    
    if not team_data:
        await update.message.reply_text(
            "🚫 <b>Not a Captain!</b> You need to be a team captain to view this. 🧑‍✈️",
            parse_mode="HTML"
        )
        return

    players_list = "\n- ".join([f"{p['name']} ({p.get('role', 'Player')}) - ₹{p.get('purchase_price', 0)}" for p in team_data["players"]])
    message = (
        f"🏟️ <b>{team_name}'s Team Details</b> 🏟️\n\n"
        f"💰 Remaining Purse: <b>₹{team_data['purse']}</b>\n"
        f"👥 Players Bought:\n- {players_list or 'No players yet.'}\n\n"
        f"Use <code>/team_stats</code> for detailed analytics! 📊"
    )
    await update.message.reply_text(message, parse_mode="HTML")

async def all_teams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays information for all registered teams."""
    if not auction_data["teams"]:
        await update.message.reply_text(
            "😕 <b>No Teams Registered!</b> Register some teams with <code>/register</code>. 💼",
            parse_mode="HTML"
        )
        return

    message = "🏟️ <b>All Registered Teams</b> 🏟️\n\n"
    for team_name, team_data in auction_data["teams"].items():
        players_list = ", ".join([p["name"] for p in team_data["players"]])
        captain = "Not assigned"
        if team_data.get("captain_id"):
            try:
                user_profile = await get_user_profile(context, team_data["captain_id"])
                captain = f"@{user_profile['username'] or user_profile['full_name']}"
            except:
                captain = "Unknown"
        message += (
            f"🏏 <b>{team_name}</b>\n"
            f"💰 Purse: <b>₹{team_data['purse']}</b>\n"
            f"🧑‍✈️ Captain: {captain}\n"
            f"👥 Players: {players_list or 'None'}\n\n"
        )
    await update.message.reply_text(message, parse_mode="HTML")

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the auction history for all sold/unsold players."""
    if not auction_data["auction_history"]:
        await update.message.reply_text(
            "😕 <b>No Auction History!</b> Start an auction to build the history. 📜",
            parse_mode="HTML"
        )
        return

    message = "📜 <b>Auction History</b> 📜\n\n"
    for record in auction_data["auction_history"]:
        player_details = next((p for p in auction_data["players"] if p["name"] == record["player"]), {})
        message += (
            f"- <b>{record['player']}</b> ({record['status']})\n"
            f"  💰 Price: ₹{record['price']}\n"
            f"  🏆 Team: {record['team']}\n"
            f"  🕒 Time: {record.get('timestamp', 'N/A')}\n\n"
        )
    await update.message.reply_text(message, parse_mode="HTML")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the leaderboard based on players and remaining purse."""
    if not auction_data["teams"]:
        await update.message.reply_text(
            "😕 <b>Empty Leaderboard!</b> No teams are registered yet. 💼",
            parse_mode="HTML"
        )
        return

    sorted_teams = sorted(
        auction_data["teams"].items(), key=lambda t: len(t[1]["players"]), reverse=True
    )

    message = "🏆 <b>Leaderboard</b> 🏆\n\n"
    for i, (team_name, team) in enumerate(sorted_teams, 1):
        message += (
            f"{i}. <b>{team_name}</b>\n"
            f"👥 {len(team['players'])} players\n"
            f"💰 Purse: ₹{team['purse']}\n\n"
        )
    await update.message.reply_text(message, parse_mode="HTML")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enhanced settings management."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can modify settings. 🛡️",
            parse_mode="HTML"
        )
        return
    
    settings = auction_data["settings"]
    
    if context.args:
        try:
            setting = context.args[0].lower()
            value = context.args[1]
            
            if setting == "timer":
                settings["timer_duration"] = max(10, min(120, int(value)))
            elif setting == "reminder":
                settings["reminder_time"] = max(1, min(10, int(value)))
            elif setting == "auto_next":
                settings["auto_next"] = value.lower() in ["true", "yes", "1"]
            elif setting == "photos":
                settings["show_player_photos"] = value.lower() in ["true", "yes", "1"]
            elif setting == "increment":
                settings["bidding_increment"] = max(0.1, float(value))
            else:
                await update.message.reply_text(
                    "❌ <b>Unknown Setting!</b> Use: timer, reminder, auto_next, photos, increment",
                    parse_mode="HTML"
                )
                return
            
            save_data()
            await update.message.reply_text(
                f"✅ <b>Success!</b> Setting '{setting}' updated to '{value}'! ⚙️",
                parse_mode="HTML"
            )
            
        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ <b>Invalid Format!</b> Use: <code>/settings &lt;setting&gt; &lt;value&gt;</code>",
                parse_mode="HTML"
            )
            return
    
    settings_text = (
        f"⚙️ <b>Bot Settings</b> ⚙️\n\n"
        f"🕒 Timer Duration: <b>{settings['timer_duration']}s</b>\n"
        f"⏰ Reminder Time: <b>{settings['reminder_time']}s</b>\n"
        f"🔄 Auto Next: <b>{settings['auto_next']}</b>\n"
        f"📸 Show Photos: <b>{settings['show_player_photos']}</b>\n"
        f"💰 Bid Increment: <b>₹{settings['bidding_increment']}</b>\n\n"
        f"<b>Usage:</b> <code>/settings timer 45</code>"
    )
    
    await update.message.reply_text(settings_text, parse_mode="HTML")

async def player_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display detailed current player information."""
    if not auction_data["is_auction_live"]:
        await update.message.reply_text(
            "❌ <b>No Active Auction!</b> Wait for the next player. 😊",
            parse_mode="HTML"
        )
        return
    
    player = auction_data["current_player"]
    
    user_profile = None
    if player.get('id'):
        try:
            user_profile = await get_user_profile(context, int(player['id']))
        except:
            pass
    
    player_name = user_profile["full_name"] if user_profile else player.get('name', 'Unknown')
    username = user_profile["username"] if user_profile else player.get('username', 'N/A')
    
    info_text = (
        f"👤 <b>Player Profile</b> 👤\n\n"
        f"🏷️ <b>Name:</b> {player_name}\n"
        f"📱 <b>Username:</b> @{username}\n"
        f"🏏 <b>Role:</b> {player.get('role', 'All-rounder')}\n"
        f"💰 <b>Base Price:</b> ₹{player.get('base_price', 0)}\n"
        f"⭐ <b>Rating:</b> {player.get('rating', 'N/A')}/10\n"
        f"🎯 <b>Speciality:</b> {player.get('speciality', 'Versatile Player')}\n"
        f"🆔 <b>Telegram ID:</b> {player.get('id', 'N/A')}\n\n"
        f"💸 <b>Current Bid:</b> ₹{auction_data['current_bid']}\n"
        f"👑 <b>Highest Bidder:</b> {auction_data['highest_bidder_name'] or 'None'}"
    )
    
    if auction_data["bidding_timer"]:
        time_left = auction_data["bidding_timer"] - datetime.now()
        seconds_left = max(0, int(time_left.total_seconds()))
        info_text += f"\n⏰ <b>Time Left:</b> {seconds_left}s"
    
    await update.message.reply_text(info_text, parse_mode="HTML")

async def team_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display detailed team statistics."""
    user_id = update.effective_user.id
    
    team_name = None
    team_data = None
    for name, data in auction_data["teams"].items():
        if data.get("captain_id") == user_id:
            team_name = name
            team_data = data
            break
    
    if not team_data:
        await update.message.reply_text(
            "🚫 <b>Not a Captain!</b> Only team captains can view detailed team stats! 🧑‍✈️",
            parse_mode="HTML"
        )
        return
    
    stats = calculate_team_stats(team_data)
    
    stats_text = (
        f"📊 <b>{team_name} - Detailed Stats</b> 📊\n\n"
        f"👥 <b>Squad Overview:</b>\n"
        f"• Total Players: {stats['total_players']}\n"
        f"• Total Spent: ₹{stats['total_spent']:.1f}\n"
        f"• Remaining Purse: ₹{stats['remaining_purse']:.1f}\n"
        f"• Average Price: ₹{stats['average_price']:.1f}\n\n"
    )
    
    if stats['most_expensive']:
        stats_text += (
            f"💎 <b>Most Expensive:</b>\n"
            f"{stats['most_expensive']['name']} ({stats['most_expensive'].get('role', 'Player')}) - "
            f"₹{stats['most_expensive'].get('purchase_price', 0)}\n\n"
        )
    
    if stats['role_distribution']:
        stats_text += f"🏏 <b>Role Distribution:</b>\n"
        for role, count in stats['role_distribution'].items():
            stats_text += f"• {role}: {count}\n"
        stats_text += "\n"
    
    if team_data['players']:
        stats_text += f"👥 <b>Complete Squad:</b>\n"
        for player in team_data['players']:
            price = player.get('purchase_price', 0)
            stats_text += f"• {player['name']} ({player.get('role', 'Player')}) - ₹{price}\n"
    
    await update.message.reply_text(stats_text, parse_mode="HTML")

async def auction_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display comprehensive auction statistics."""
    stats = auction_data["auction_stats"]
    
    stats_text = (
        f"📈 <b>Auction Analytics</b> 📈\n\n"
        f"🎯 <b>Overall Stats:</b>\n"
        f"• Total Auctions: {stats['total_auctions']}\n"
        f"• Players Sold: {len(auction_data['sold_players'])}\n"
        f"• Players Unsold: {len(auction_data['unsold_players'])}\n"
        f"• Success Rate: {(len(auction_data['sold_players']) / max(1, stats['total_auctions']) * 100):.1f}%\n\n"
        f"💰 <b>Financial Stats:</b>\n"
        f"• Total Spent: ₹{stats['total_amount_spent']:.1f}\n"
        f"• Average Price: ₹{stats['average_price']:.1f}\n"
        f"• Highest Bid: ₹{stats['highest_bid']}\n\n"
    )
    
    if stats['most_expensive_player']:
        stats_text += (
            f"💎 <b>Most Expensive Player:</b>\n"
            f"{stats['most_expensive_player']['name']} ({stats['most_expensive_player'].get('role', 'Player')}) - "
            f"₹{stats['most_expensive_player']['purchase_price']} "
            f"(to {stats['most_expensive_player']['purchased_by']})\n\n"
        )
    
    if auction_data["teams"]:
        stats_text += f"🏆 <b>Team Spending:</b>\n"
        team_spending = []
        for name, data in auction_data["teams"].items():
            team_stats = calculate_team_stats(data)
            team_spending.append((name, team_stats['total_spent']))
        
        team_spending.sort(key=lambda x: x[1], reverse=True)
        for name, spent in team_spending[:5]:
            stats_text += f"• {name}: ₹{spent:.1f}\n"
    
    await update.message.reply_text(stats_text, parse_mode="HTML")

async def reset_auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset entire auction (admin only)."""
    user_id = update.effective_user.id
    if user_id not in auction_data["admin_ids"]:
        await update.message.reply_text(
            "🚫 <b>Access Denied!</b> Only admins can reset the auction! 🛡️",
            parse_mode="HTML"
        )
        return
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Reset All", callback_data="confirm_reset"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_reset")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚠️ <b>Warning!</b> This will reset the entire auction:\n\n"
        "• Clear all auction history 📜\n"
        "• Reset all team squads 🏟️\n"
        "• Restore original purses 💰\n"
        "• Reset all statistics 📊\n\n"
        "Are you sure?",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle private messages to guide users."""
    await update.message.reply_text(
        "🏏 <b>Cricket Auction Bot</b> 🏏\n\n"
        "Please use commands in the group chat where the auction is running! 🏟️\n"
        "Use <code>/help</code> in the group for a list of commands. 📚",
        parse_mode="HTML"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("quick_bid_"):
        bid_amount = float(data.replace("quick_bid_", ""))
        
        team_name = None
        team_data = None
        for name, data in auction_data["teams"].items():
            if data.get("captain_id") == user_id:
                team_name = name
                team_data = data
                break
        
        if not team_data:
            await query.edit_message_text(
                "🚫 <b>Not a Captain!</b> You need to be a team captain to bid! 🧑‍✈️",
                parse_mode="HTML"
            )
            return
        
        if not auction_data["is_auction_live"]:
            await query.edit_message_text(
                "❌ <b>No Active Auction!</b> Wait for the next player. 😊",
                parse_mode="HTML"
            )
            return
        
        min_increment = auction_data["settings"]["bidding_increment"]
        if bid_amount < auction_data["current_bid"] + min_increment:
            await query.edit_message_text(
                f"❌ <b>Bid Too Low!</b> Minimum bid: ₹{auction_data['current_bid'] + min_increment} 💰",
                parse_mode="HTML"
            )
            return
        
        if team_data["purse"] < bid_amount:
            await query.edit_message_text(
                f"❌ <b>Insufficient Funds!</b> Available: ₹{team_data['purse']} 😕",
                parse_mode="HTML"
            )
            return
        
        auction_data["current_bid"] = bid_amount
        auction_data["highest_bidder_id"] = user_id
        auction_data["highest_bidder_name"] = team_name
        
        player = auction_data["current_player"]
        remaining = team_data["purse"] - bid_amount
        
        await context.bot.send_message(
            chat_id=auction_data["main_chat_id"],
            text=(
                f"⚡ <b>QUICK BID!</b> ⚡\n\n"
                f"💰 <b>{team_name}</b> bids <b>₹{bid_amount}</b>\n"
                f"👤 Player: <b>{player['name']}</b> ({player.get('role', 'Player')})\n"
                f"💵 Remaining: ₹{remaining:.1f}\n\n"
                f"⏰ Time remaining: <b>{int((auction_data['bidding_timer'] - datetime.now()).total_seconds())}s</b>"
            ),
            parse_mode="HTML"
        )
        
        reset_timer()
        await start_timer(context, auction_data["settings"]["timer_duration"])
        save_data()
        
        await query.edit_message_text(
            f"✅ <b>Quick Bid Placed!</b> ₹{bid_amount} for {player['name']} 💸",
            parse_mode="HTML"
        )
    
    elif data == "player_profile":
        if not auction_data["is_auction_live"]:
            await query.edit_message_text(
                "❌ <b>No Active Auction!</b> Wait for the next player. 😊",
                parse_mode="HTML"
            )
            return
        
        player = auction_data["current_player"]
        user_profile = None
        
        if player.get('id'):
            try:
                user_profile = await get_user_profile(context, int(player['id']))
            except:
                pass
        
        profile_text = (
            f"👤 <b>Detailed Player Profile</b> 👤\n\n"
            f"🏷️ <b>Name:</b> {user_profile['full_name'] if user_profile else player.get('name', 'Unknown')}\n"
            f"📱 <b>Username:</b> @{user_profile['username'] if user_profile else player.get('username', 'N/A')}\n"
            f"🆔 <b>ID:</b> {player.get('id', 'N/A')}\n"
            f"🏏 <b>Role:</b> {player.get('role', 'All-rounder')}\n"
            f"⭐ <b>Rating:</b> {player.get('rating', 'N/A')}/10\n"
            f"🎯 <b>Speciality:</b> {player.get('speciality', 'Versatile Player')}\n"
            f"💰 <b>Base Price:</b> ₹{player.get('base_price', 0)}\n"
            f"💸 <b>Current Bid:</b> ₹{auction_data['current_bid']}\n"
            f"👑 <b>Highest Bidder:</b> {auction_data['highest_bidder_name'] or 'None'}"
        )
        
        await query.edit_message_text(profile_text, parse_mode="HTML")
    
    elif data == "setup_guide":
        await query.edit_message_text(
            "📚 <b>Quick Setup Guide</b> 📚\n\n"
            "1. <b>Claim Ownership</b>: Use <code>/set_owner</code> to become the bot owner. 👑\n"
            "2. <b>Add Admins</b>: Use <code>/add_admin</code> while replying to users. 🛡️\n"
            "3. <b>Register Teams</b>: Use <code>/register &lt;TeamName&gt; &lt;Purse&gt;</code>. 💼\n"
            "4. <b>Assign Captains</b>: Use <code>/set_captain &lt;TeamName&gt;</code> while replying. 🧑‍✈️\n"
            "5. <b>Load Players</b>: Use <code>/load_players &lt;file.csv&gt;</code>. 📋\n"
            "6. <b>Start Auction</b>: Use <code>/start_auction</code> to begin! 🎬\n\n"
            "Use <code>/help</code> for detailed command info! 📜",
            parse_mode="HTML"
        )
    
    elif data == "bot_settings":
        settings = auction_data["settings"]
        settings_text = (
            f"⚙️ <b>Bot Settings</b> ⚙️\n\n"
            f"🕒 Timer Duration: <b>{settings['timer_duration']}s</b>\n"
            f"⏰ Reminder Time: <b>{settings['reminder_time']}s</b>\n"
            f"🔄 Auto Next: <b>{settings['auto_next']}</b>\n"
            f"📸 Show Photos: <b>{settings['show_player_photos']}</b>\n"
            f"💰 Bid Increment: <b>₹{settings['bidding_increment']}</b>\n\n"
            f"Admins can change settings with <code>/settings &lt;setting&gt; &lt;value&gt;</code>"
        )
        await query.edit_message_text(settings_text, parse_mode="HTML")
    
    elif data == "auction_stats":
        await auction_stats_command(update, context)
    
    elif data == "confirm_reset":
        if user_id not in auction_data["admin_ids"]:
            await query.edit_message_text(
                "🚫 <b>Access Denied!</b> Only admins can reset the auction! 🛡️",
                parse_mode="HTML"
            )
            return
        
        reset_timer()
        auction_data.update({
            "is_auction_live": False,
            "current_player": None,
            "current_bid": 0.0,
            "highest_bidder_id": None,
            "highest_bidder_name": None,
            "sold_players": [],
            "unsold_players": [],
            "auction_history": [],
            "auction_stats": {
                "total_auctions": 0,
                "total_amount_spent": 0,
                "highest_bid": 0,
                "most_expensive_player": None,
                "average_price": 0
            }
        })
        
        for team_data in auction_data["teams"].values():
            original_purse = team_data.get("original_purse", team_data.get("purse", 1000))
            team_data["purse"] = original_purse
            team_data["players"] = []
        
        auction_data["available_players"] = list(auction_data["players"])
        
        save_data()
        await query.edit_message_text(
            "✅ <b>Auction Reset Complete!</b> All data has been cleared. 🔄",
            parse_mode="HTML"
        )
    
    elif data == "cancel_reset":
        await query.edit_message_text(
            "❌ <b>Reset Cancelled!</b> Auction data remains unchanged. 😊",
            parse_mode="HTML"
        )

def export_enhanced_results():
    """Export enhanced auction results to CSV."""
    if not auction_data["auction_history"]:
        logger.info("No auction history to export.")
        return
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"auction_results_{timestamp}.csv"
    
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["Auction_No", "Player", "Role", "Base_Price", "Final_Price", "Team", "Status", "Timestamp", "Player_ID", "Username"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for i, record in enumerate(auction_data["auction_history"], 1):
            player_details = {}
            for player in auction_data["players"]:
                if player["name"] == record["player"]:
                    player_details = player
                    break
            
            writer.writerow({
                "Auction_No": i,
                "Player": record["player"],
                "Role": player_details.get("role", "Unknown"),
                "Base_Price": player_details.get("base_price", 0),
                "Final_Price": record["price"],
                "Team": record["team"],
                "Status": record["status"],
                "Timestamp": record.get("timestamp", ""),
                "Player_ID": player_details.get("id", ""),
                "Username": player_details.get("username", "")
            })
    
    logger.info(f"Enhanced results exported to {filename}")

async def show_final_results(bot, chat_id):
    """Display comprehensive final auction results."""
    stats = auction_data["auction_stats"]
    
    final_message = (
        f"🏁 <b>AUCTION COMPLETED!</b> 🏁\n\n"
        f"📊 <b>Final Statistics:</b>\n"
        f"• Total Auctions: {stats['total_auctions']}\n"
        f"• Players Sold: {len(auction_data['sold_players'])}\n"
        f"• Players Unsold: {len(auction_data['unsold_players'])}\n"
        f"• Total Amount: ₹{stats['total_amount_spent']:.1f}\n"
        f"• Average Price: ₹{stats['average_price']:.1f}\n"
        f"• Highest Bid: ₹{stats['highest_bid']}\n\n"
    )
    
    if stats['most_expensive_player']:
        final_message += (
            f"💎 <b>Most Expensive:</b>\n"
            f"{stats['most_expensive_player']['name']} ({stats['most_expensive_player'].get('role', 'Player')}) - "
            f"₹{stats['most_expensive_player']['purchase_price']} (to {stats['most_expensive_player']['purchased_by']})\n\n"
        )
    
    final_message += "Use <code>/leaderboard</code> for final team standings! 🏆"
    
    await bot.send_message(chat_id=chat_id, text=final_message, parse_mode="HTML")

# Set up the application globally for timer functions
application = None

def main() -> None:
    """Enhanced main function with better error handling."""
    global application
    
    load_data()
    
    token = "8499954180:AAE8O1Q8iukvxCxjRiQbxE4GPxNntR2HrNg"
    
    try:
        application = ApplicationBuilder().token(token).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("set_owner", set_owner))
        application.add_handler(CommandHandler("add_admin", add_admin))
        application.add_handler(CommandHandler("register", register))
        application.add_handler(CommandHandler("set_captain", set_captain))
        application.add_handler(CommandHandler("load_players", load_players))
        application.add_handler(CommandHandler("start_auction", start_auction_manual))
        application.add_handler(CommandHandler("end_auction", end_auction_command))
        application.add_handler(CommandHandler("bid", enhanced_bid_command))
        application.add_handler(CommandHandler("quick_bid", quick_bid_command))
        application.add_handler(CommandHandler("skip", skip_player))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("my_team", my_team))
        application.add_handler(CommandHandler("teams", all_teams))
        application.add_handler(CommandHandler("history", history))
        application.add_handler(CommandHandler("leaderboard", leaderboard))
        application.add_handler(CommandHandler("settings", settings_command))
        application.add_handler(CommandHandler("player_info", player_info_command))
        application.add_handler(CommandHandler("team_stats", team_stats_command))
        application.add_handler(CommandHandler("auction_stats", auction_stats_command))
        application.add_handler(CommandHandler("reset_auction", reset_auction_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
        application.add_handler(CallbackQueryHandler(button_callback))
        
        logger.info("Starting enhanced auction bot...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()


