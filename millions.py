import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID')  # Опционально, можно не указывать

# Настройка интентов
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Создание бота
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} успешно запущен!')
    print(f'🆔 ID бота: {bot.user.id}')
    
    # Синхронизация команд
    try:
        synced = await bot.tree.sync()
        print(f'🔄 Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'❌ Ошибка синхронизации: {e}')

# Команда /sett
@bot.tree.command(name="sett", description="Настройка сервера: создание ролей и каналов")
@app_commands.checks.has_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    """Команда для настройки сервера - создает роли и каналы"""
    
    # Отправляем "думаю..." и делаем команду видимой только вызывающему
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    
    try:
        # 1. СОЗДАНИЕ РОЛЕЙ
        print(f"🔨 Создаю роли на сервере {guild.name}...")
        
        roles_info = {}
        
        # Админские роли (2 штуки)
        admin_role1 = await guild.create_role(
            name="Админ-1",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.red(),
            reason="Настройка сервера через /sett"
        )
        roles_info['admin1'] = admin_role1
        
        admin_role2 = await guild.create_role(
            name="Админ-2",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.blue(),
            reason="Настройка сервера через /sett"
        )
        roles_info['admin2'] = admin_role2
        
        # Обычные роли (2 штуки)
        normal_role1 = await guild.create_role(
            name="Пользователь-1",
            permissions=discord.Permissions(
                send_messages=True,
                read_messages=True,
                view_channel=True,
                connect=True,
                speak=True
            ),
            color=discord.Color.green(),
            reason="Настройка сервера через /sett"
        )
        roles_info['normal1'] = normal_role1
        
        normal_role2 = await guild.create_role(
            name="Пользователь-2",
            permissions=discord.Permissions(
                send_messages=True,
                read_messages=True,
                view_channel=True,
                connect=True,
                speak=True
            ),
            color=discord.Color.orange(),
            reason="Настройка сервера через /sett"
        )
        roles_info['normal2'] = normal_role2
        
        print(f"✅ Создано 4 роли")
        
        # 2. БАЗОВЫЕ ПРАВА ДОСТУПА
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True),
            normal_role1: discord.PermissionOverwrite(view_channel=True),
            normal_role2: discord.PermissionOverwrite(view_channel=True)
        }
        
        # 3. СОЗДАНИЕ ТЕКСТОВЫХ КАНАЛОВ
        
        # 1.1 News - видят все, пишут только админы
        news_overwrites = base_overwrites.copy()
        news_overwrites[normal_role1] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False  # Не могут писать
        )
        news_overwrites[normal_role2] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False  # Не могут писать
        )
        
        news_channel = await guild.create_text_channel(
            name="news",
            topic="📢 Новости сервера (только для чтения)",
            overwrites=news_overwrites,
            reason="Канал News из команды /sett"
        )
        
        # 1.2 Flood - видят и пишут все
        flood_overwrites = base_overwrites.copy()
        flood_overwrites[normal_role1] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True  # Могут писать
        )
        flood_overwrites[normal_role2] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True  # Могут писать
        )
        
        flood_channel = await guild.create_text_channel(
            name="flood",
            topic="💬 Общий чат для всех",
            overwrites=flood_overwrites,
            reason="Канал Flood из команды /sett"
        )
        
        # 1.3 Tags - админы пишут, обычные только смотрят
        tags_overwrites = base_overwrites.copy()
        tags_overwrites[normal_role1] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False  # Не могут писать
        )
        tags_overwrites[normal_role2] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False  # Не могут писать
        )
        
        tags_channel = await guild.create_text_channel(
            name="tags",
            topic="🏷️ Теги (только для админов)",
            overwrites=tags_overwrites,
            reason="Канал Tags из команды /sett"
        )
        
        # 1.4 Media - все могут писать
        media_overwrites = base_overwrites.copy()
        media_overwrites[normal_role1] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,  # Могут писать
            attach_files=True    # Могут прикреплять файлы
        )
        media_overwrites[normal_role2] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,  # Могут писать
            attach_files=True    # Могут прикреплять файлы
        )
        
        media_channel = await guild.create_text_channel(
            name="media",
            topic="🖼️ Медиа-контент",
            overwrites=media_overwrites,
            reason="Канал Media из команды /sett"
        )
        
        print(f"✅ Создано 4 публичных текстовых канала")
        
        # 4. ЗАКРЫТЫЕ КАНАЛЫ (ТОЛЬКО ДЛЯ АДМИНОВ)
        
        # 1.5 Logs - только для админов
        logs_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            normal_role1: discord.PermissionOverwrite(view_channel=False),
            normal_role2: discord.PermissionOverwrite(view_channel=False)
        }
        
        logs_channel = await guild.create_text_channel(
            name="logs",
            topic="📊 Логи сервера (только для админов)",
            overwrites=logs_overwrites,
            reason="Канал Logs из команды /sett"
        )
        
        # 1.6 High-flood - только для админов
        high_flood_overwrites = logs_overwrites.copy()
        
        high_flood_channel = await guild.create_text_channel(
            name="high-flood",
            topic="🚨 Высокоуровневый чат (только для админов)",
            overwrites=high_flood_overwrites,
            reason="Канал High-flood из команды /sett"
        )
        
        print(f"✅ Создано 2 закрытых текстовых канала")
        
        # 5. ГОЛОСОВЫЕ КАНАЛЫ (4 штуки)
        voice_overwrites = base_overwrites.copy()
        voice_overwrites[normal_role1] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True
        )
        voice_overwrites[normal_role2] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True
        )
        
        voice_channels = []
        for i in range(1, 5):
            voice_channel = await guild.create_voice_channel(
                name=f"Голосовой-{i}",
                overwrites=voice_overwrites,
                reason=f"Голосовой канал {i} из команды /sett"
            )
            voice_channels.append(voice_channel)
        
        print(f"✅ Создано 4 голосовых канала")
        
        # 6. ОТЧЕТ О ВЫПОЛНЕНИИ
        embed = discord.Embed(
            title="🎉 Настройка сервера завершена!",
            description="Все элементы успешно созданы:",
            color=discord.Color.green()
        )
        
        # Добавляем информацию о ролях
        roles_text = "\n".join([
            f"• {roles_info['admin1'].mention} (Админ)",
            f"• {roles_info['admin2'].mention} (Админ)",
            f"• {roles_info['normal1'].mention} (Пользователь)",
            f"• {roles_info['normal2'].mention} (Пользователь)"
        ])
        embed.add_field(name="👥 **Роли**", value=roles_text, inline=False)
        
        # Добавляем информацию о текстовых каналах
        public_channels = [
            f"• {news_channel.mention} - видят все, пишут админы",
            f"• {flood_channel.mention} - видят и пишут все",
            f"• {tags_channel.mention} - админы пишут, остальные читают",
            f"• {media_channel.mention} - все могут писать и отправлять файлы"
        ]
        embed.add_field(name="💬 **Публичные каналы**", value="\n".join(public_channels), inline=False)
        
        # Добавляем информацию о закрытых каналах
        private_channels = [
            f"• {logs_channel.mention} - только для админов",
            f"• {high_flood_channel.mention} - только для админов"
        ]
        embed.add_field(name="🔒 **Закрытые каналы**", value="\n".join(private_channels), inline=False)
        
        # Добавляем информацию о голосовых каналах
        voice_text = "\n".join([f"• {vc.mention}" for vc in voice_channels])
        embed.add_field(name="🎤 **Голосовые каналы**", value=voice_text, inline=False)
        
        embed.set_footer(text=f"Настроено пользователем {interaction.user} • Бот {bot.user.name}")
        embed.set_thumbnail(url=guild.icon.url if guild.icon else bot.user.avatar.url)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        print(f"✅ Настройка сервера '{guild.name}' завершена успешно!")
        
    except discord.Forbidden:
        error_msg = "❌ У бота недостаточно прав! Нужны права администратора."
        await interaction.followup.send(error_msg, ephemeral=True)
        print("❌ Ошибка: недостаточно прав у бота")
        
    except Exception as e:
        error_msg = f"❌ Произошла ошибка: {str(e)}"
        await interaction.followup.send(error_msg, ephemeral=True)
        print(f"❌ Ошибка при настройке: {e}")

# Обработчик ошибок для команды
@setup_server.error
async def setup_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ У вас недостаточно прав для выполнения этой команды! Требуются права администратора.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ Произошла ошибка: {str(error)}",
            ephemeral=True
        )

# Простая команда для проверки работы бота
@bot.tree.command(name="ping", description="Проверка работы бота")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🏓 Понг! Задержка: {round(bot.latency * 1000)}мс",
        ephemeral=True
    )

# Запуск бота
if __name__ == "__main__":
    print("🚀 Запуск Discord бота...")
    bot.run(TOKEN)