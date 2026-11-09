import os
import asyncio
import uuid
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
import time
from agent.engine import MarcusEngine

from agent.crash_handler import CrashRecorder

crash_recorder = CrashRecorder()
crash_recorder.install()


load_dotenv()

start_time = time.time()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_TELEGRAM_USER_ID", "0"))

if not BOT_TOKEN:
    print("[Error]: TELEGRAM_BOT_TOKEN is missing in your .env file!")
    print("Please set TELEGRAM_BOT_TOKEN and ALLOWED_TELEGRAM_USER_ID in .env before running marcus_bot.py")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

pending_approvals = {}


async def telegram_approval_callback(tool_name: str, tool_args: dict) -> bool:
    """Asks the user for approval via interactive Telegram buttons."""
    if not bot or not ALLOWED_USER_ID:
        return False

    approval_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_approvals[approval_id] = future

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"appr:{approval_id}:yes"),
            InlineKeyboardButton(text="❌ Deny", callback_data=f"appr:{approval_id}:no")
        ]
    ])

    preview = str(tool_args)[:400]
    await bot.send_message(
        chat_id=ALLOWED_USER_ID,
        text=f"⚠️ **Approval Required**\n\n**Tool:** `{tool_name}`\n**Args:** `{preview}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )

    try:
        # Wait up to 120 seconds for the user to press a button
        approved = await asyncio.wait_for(future, timeout=120)
        return approved
    except asyncio.TimeoutError:
        return False
    finally:
        pending_approvals.pop(approval_id, None)

marcus = MarcusEngine(approval_callback=telegram_approval_callback)


@dp.callback_query(F.data.startswith("appr:"))
async def handle_approval_button(callback: types.CallbackQuery):
    _, approval_id, decision = callback.data.split(":")
    future = pending_approvals.get(approval_id)

    if future and not future.done():
        is_approved = (decision == "yes")
        future.set_result(is_approved)
        status_text = "✅ Approved" if is_approved else "❌ Denied"
        await callback.message.edit_text(f"{callback.message.text}\n\n**Decision:** {status_text}")
    await callback.answer()


@dp.message(Command("status", "ping"))
async def handle_status(message: types.Message):
    # Security: Ignore any messages from strangers
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        await message.reply("Access denied.")
        return

    uptime_sec = int(time.time() - start_time)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"
    await message.reply(
        f"🟢 **Marcus is Online**\n\n"
        f"⏱️ **Uptime:** {uptime_str}\n"
        f"🛠️ **Tools:** {marcus.mcp_manager.tool_count} loaded\n"
        f"⚡ **Model:** {marcus.model}"
    )


@dp.message()
async def handle_user_message(message: types.Message):
    # Security: Ignore any messages from strangers
    if ALLOWED_USER_ID and message.from_user.id != ALLOWED_USER_ID:
        await message.reply("Access denied.")
        return

    # Indicate Marcus is working
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    status_msg = await message.reply("⏳ Marcus is thinking...")

    try:
        # Process user request through Marcus Core Engine
        response = await marcus.process_message(message.text)

        # Telegram has a 4096-char message limit
        if len(response) <= 4000:
            await status_msg.edit_text(response)
        else:
            await status_msg.delete()
            for chunk in [response[i:i + 4000] for i in range(0, len(response), 4000)]:
                await message.answer(chunk)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")


async def main():
    if not BOT_TOKEN:
        print("[System Info]: Cannot start bot because TELEGRAM_BOT_TOKEN is missing in .env.")
        return

    print("[System Info]: Connecting Marcus Engine...")
    await marcus.initialize()
    print(f"[System Info]: Loaded {marcus.mcp_manager.tool_count} tools from {marcus.mcp_manager.server_count} MCP servers.")

    try:
        await bot.set_my_commands([
            BotCommand(command="status", description="Check status, uptime & tools")
        ])
    except Exception as e:
        print(f"[System Warning]: Could not set bot commands: {e}")

    if ALLOWED_USER_ID:
        try:
            await bot.send_message(ALLOWED_USER_ID, "🟢 **Marcus is Online**")
        except Exception as e:
            print(f"[System Error]: Could not send startup message: {e}")
    else:
        print("[System Info]: No ALLOWED_USER_ID found in .env file. Bot will not accept messages.")

    try:
        await dp.start_polling(bot)
    finally:
        if ALLOWED_USER_ID:
            try:
                await bot.send_message(ALLOWED_USER_ID, "🔴 **Marcus is shutting down.**")
            except Exception as e:
                print(f"[System Error]: Could not send shutdown message: {e}")
        await marcus.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[System Info]: Bot stopped.")
