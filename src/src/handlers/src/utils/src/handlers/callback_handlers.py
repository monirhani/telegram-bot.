from pyrogram import filters

def register_handlers(bot):
    @bot.on_message(filters.command("start"))
    async def start_command(client, message):
        await message.reply_text("🤖 Dragon Bot Started!")
