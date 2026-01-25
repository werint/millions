import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import asyncio
import json
from dotenv import load_dotenv
import logging
import sys

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    logger.error("❌ ОШИБКА: DISCORD_TOKEN не найден!")
    logger.error("💡 Установите переменную в настройках Railway:")
    logger.error("   1. Перейдите в Settings вашего приложения")
    logger.error("   2. Добавьте Variables: DISCORD_TOKEN=ваш_токен_бота")
    logger.error("   3. Нажмите Add")
    sys.exit(1)

logger.info("✅ DISCORD_TOKEN найден")

# Настройка интентов
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Создание бота
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = None
        self.use_sqlite = False
        self.connect()
        if self.conn:
            self.init_database()
    
    def get_database_url(self):
        """Получить строку подключения к PostgreSQL из Railway"""
        database_url = os.getenv('DATABASE_URL')
        
        if database_url:
            logger.info("🔗 Использую DATABASE_URL от Railway")
            if database_url.startswith('postgresql://'):
                database_url = database_url.replace('postgresql://', 'postgres://')
            return database_url
        
        db_host = os.getenv('PGHOST')
        db_name = os.getenv('PGDATABASE')
        db_user = os.getenv('PGUSER')
        db_password = os.getenv('PGPASSWORD')
        db_port = os.getenv('PGPORT', 5432)
        
        if all([db_host, db_name, db_user, db_password]):
            logger.info("🔗 Использую отдельные переменные PostgreSQL")
            return f"postgres://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        logger.warning("⚠️ Переменные PostgreSQL не найдены, использую SQLite")
        return None
    
    def connect(self):
        """Подключение к базе данных"""
        try:
            database_url = self.get_database_url()
            
            if database_url:
                try:
                    import psycopg2
                    from psycopg2.extras import RealDictCursor
                    
                    logger.info("🔄 Подключение к PostgreSQL...")
                    self.conn = psycopg2.connect(
                        database_url,
                        sslmode='require',
                        cursor_factory=RealDictCursor
                    )
                    logger.info("✅ Подключено к PostgreSQL (Railway)")
                    return
                except ImportError:
                    logger.error("❌ psycopg2 не установлен")
                    logger.info("💡 Запустите: pip install psycopg2-binary")
                except Exception as e:
                    logger.error(f"❌ Ошибка PostgreSQL: {e}")
            
            logger.info("🔄 Использую SQLite как временное решение...")
            import sqlite3
            self.use_sqlite = True
            self.conn = sqlite3.connect('bot_database.db')
            self.conn.row_factory = sqlite3.Row
            logger.info("✅ Создана SQLite база: bot_database.db")
            logger.warning("⚠️ SQLite для разработки. Для продакшена добавьте PostgreSQL в Railway:")
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка подключения к БД: {e}")
            sys.exit(1)
    
    def execute(self, query, params=None, fetchone=False, fetchall=False, commit=True):
        """Выполнение SQL запроса"""
        try:
            cursor = self.conn.cursor()
            
            if self.use_sqlite:
                query = query.replace('%s', '?')
                query = query.replace('SERIAL', 'INTEGER PRIMARY KEY AUTOINCREMENT')
                query = query.replace('VARCHAR', 'TEXT')
                query = query.replace('BOOLEAN', 'INTEGER')
                query = query.replace('TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                if 'ON CONFLICT' in query:
                    query = query.split('ON CONFLICT')[0]
            
            cursor.execute(query, params or ())
            
            if fetchone:
                result = cursor.fetchone()
            elif fetchall:
                result = cursor.fetchall()
            else:
                result = cursor.rowcount
            
            if commit:
                self.conn.commit()
            
            cursor.close()
            return result
        except Exception as e:
            logger.error(f"❌ Ошибка SQL: {e}")
            raise
    
    def init_database(self):
        """Инициализация таблиц БД"""
        logger.info("🔄 Создание таблиц в базе данных...")
        
        self.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id SERIAL PRIMARY KEY,
                discord_id VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                is_setup BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.execute('''
            CREATE TABLE IF NOT EXISTS server_settings (
                id SERIAL PRIMARY KEY,
                server_id INTEGER NOT NULL,
                admin_role_1_id VARCHAR(255),
                admin_role_2_id VARCHAR(255),
                news_channel_id VARCHAR(255),
                flood_channel_id VARCHAR(255),
                tags_channel_id VARCHAR(255),
                media_channel_id VARCHAR(255),
                logs_channel_id VARCHAR(255),
                high_flood_channel_id VARCHAR(255),
                voice_channel_ids TEXT,
                high_voice_channel_id VARCHAR(255),
                main_category_id VARCHAR(255),
                high_category_id VARCHAR(255),
                UNIQUE(server_id)
            )
        ''')
        
        self.execute('''
            CREATE TABLE IF NOT EXISTS tracked_roles (
                id SERIAL PRIMARY KEY,
                server_id INTEGER NOT NULL,
                source_server_id VARCHAR(255) NOT NULL,
                source_server_name VARCHAR(255),
                source_role_id VARCHAR(255) NOT NULL,
                source_role_name VARCHAR(255),
                target_role_id VARCHAR(255),
                target_role_name VARCHAR(255),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        if self.use_sqlite:
            self.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_roles_unique 
                ON tracked_roles (server_id, source_server_id, source_role_id)
            ''')
        else:
            self.execute('''
                ALTER TABLE tracked_roles 
                ADD CONSTRAINT unique_tracked_role 
                UNIQUE (server_id, source_server_id, source_role_id)
            ''')
        
        self.execute('''
            CREATE TABLE IF NOT EXISTS user_roles (
                id SERIAL PRIMARY KEY,
                server_id INTEGER NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                tracked_role_id INTEGER NOT NULL,
                has_role BOOLEAN DEFAULT FALSE,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        if self.use_sqlite:
            self.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_user_roles_unique 
                ON user_roles (server_id, user_id, tracked_role_id)
            ''')
        
        self.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                id SERIAL PRIMARY KEY,
                server_id INTEGER NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL,
                ban_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                unban_time TIMESTAMP,
                ban_duration INTEGER DEFAULT 600,
                reason TEXT,
                is_unbanned BOOLEAN DEFAULT FALSE
            )
        ''')
        
        if self.use_sqlite:
            self.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_banned_users_unique 
                ON banned_users (server_id, user_id)
            ''')
        
        logger.info("✅ Таблицы базы данных успешно созданы/проверены")
    
    # ========== МЕТОДЫ ДЛЯ СЕРВЕРОВ ==========
    
    def get_or_create_server(self, discord_id: str, name: str) -> dict:
        """Получить или создать сервер в БД"""
        result = self.execute(
            'SELECT * FROM servers WHERE discord_id = %s',
            (discord_id,),
            fetchone=True
        )
        
        if result:
            return dict(result)
        
        try:
            self.execute(
                '''INSERT INTO servers (discord_id, name) 
                   VALUES (%s, %s)''',
                (discord_id, name)
            )
        except:
            self.execute(
                '''INSERT OR IGNORE INTO servers (discord_id, name) 
                   VALUES (%s, %s)''',
                (discord_id, name)
            )
        
        result = self.execute(
            'SELECT * FROM servers WHERE discord_id = %s',
            (discord_id,),
            fetchone=True
        )
        return dict(result) if result else None
    
    def mark_server_setup(self, discord_id: str):
        """Отметить сервер как настроенный"""
        self.execute(
            'UPDATE servers SET is_setup = TRUE WHERE discord_id = %s',
            (discord_id,)
        )
    
    # ========== МЕТОДЫ ДЛЯ НАСТРОЕК ==========
    
    def save_server_settings(self, server_id: int, settings: dict):
        """Сохранить настройки сервера"""
        voice_channel_ids = json.dumps(settings.get('voice_channel_ids', []))
        
        self.execute('''
            INSERT INTO server_settings 
            (server_id, admin_role_1_id, admin_role_2_id, news_channel_id, 
             flood_channel_id, tags_channel_id, media_channel_id, 
             logs_channel_id, high_flood_channel_id, voice_channel_ids,
             high_voice_channel_id, main_category_id, high_category_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            server_id,
            settings.get('admin_role_1_id'),
            settings.get('admin_role_2_id'),
            settings.get('news_channel_id'),
            settings.get('flood_channel_id'),
            settings.get('tags_channel_id'),
            settings.get('media_channel_id'),
            settings.get('logs_channel_id'),
            settings.get('high_flood_channel_id'),
            voice_channel_ids,
            settings.get('high_voice_channel_id'),
            settings.get('main_category_id'),
            settings.get('high_category_id')
        ))
    
    def get_server_settings(self, server_id: int) -> dict:
        """Получить настройки сервера"""
        result = self.execute(
            'SELECT * FROM server_settings WHERE server_id = %s',
            (server_id,),
            fetchone=True
        )
        return dict(result) if result else {}
    
    # ========== МЕТОДЫ ДЛЯ ОТСЛЕЖИВАЕМЫХ РОЛЕЙ ==========
    
    def add_tracked_role(self, server_id: int, source_server_id: str, source_role_id: str,
                        source_server_name: str = None, source_role_name: str = None) -> int:
        """Добавить отслеживаемую роль"""
        result = self.execute(
            '''SELECT id FROM tracked_roles 
               WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s''',
            (server_id, source_server_id, source_role_id),
            fetchone=True
        )
        
        if result:
            self.execute(
                'UPDATE tracked_roles SET is_active = TRUE WHERE id = %s',
                (result['id'],)
            )
            return result['id']
        
        self.execute('''
            INSERT INTO tracked_roles 
            (server_id, source_server_id, source_role_id, source_server_name, source_role_name)
            VALUES (%s, %s, %s, %s, %s)
        ''', (server_id, source_server_id, source_role_id, source_server_name, source_role_name))
        
        result = self.execute(
            '''SELECT id FROM tracked_roles 
               WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s''',
            (server_id, source_server_id, source_role_id),
            fetchone=True
        )
        
        return result['id'] if result else None
    
    def update_target_role(self, tracked_role_id: int, target_role_id: str, target_role_name: str):
        """Обновить целевую роль"""
        self.execute('''
            UPDATE tracked_roles 
            SET target_role_id = %s, target_role_name = %s 
            WHERE id = %s
        ''', (target_role_id, target_role_name, tracked_role_id))
    
    def get_tracked_roles(self, server_id: int) -> list:
        """Получить все отслеживаемые роли сервера"""
        results = self.execute(
            'SELECT * FROM tracked_roles WHERE server_id = %s AND is_active = TRUE',
            (server_id,),
            fetchall=True
        )
        return [dict(r) for r in results] if results else []
    
    def deactivate_tracked_role(self, tracked_role_id: int):
        """Деактивировать отслеживаемую роль"""
        self.execute(
            'UPDATE tracked_roles SET is_active = FALSE WHERE id = %s',
            (tracked_role_id,)
        )
    
    # ========== МЕТОДЫ ДЛЯ БАНОВ ==========
    
    def ban_user(self, server_id: int, user_id: str, username: str, reason: str = None) -> int:
        """Забанить пользователя"""
        unban_time = datetime.now() + timedelta(seconds=600)
        
        self.execute('''
            INSERT OR REPLACE INTO banned_users 
            (server_id, user_id, username, unban_time, reason)
            VALUES (%s, %s, %s, %s, %s)
        ''', (server_id, user_id, username, unban_time.isoformat(), reason))
        
        result = self.execute(
            'SELECT id FROM banned_users WHERE server_id = %s AND user_id = %s',
            (server_id, user_id),
            fetchone=True
        )
        
        return result['id'] if result else None
    
    def unban_user(self, server_id: int, user_id: str):
        """Разбанить пользователя"""
        self.execute('''
            UPDATE banned_users 
            SET is_unbanned = TRUE, unban_time = CURRENT_TIMESTAMP
            WHERE server_id = %s AND user_id = %s AND is_unbanned = FALSE
        ''', (server_id, user_id))
    
    def get_banned_users(self, server_id: int) -> list:
        """Получить забаненных пользователей"""
        results = self.execute(
            'SELECT * FROM banned_users WHERE server_id = %s AND is_unbanned = FALSE',
            (server_id,),
            fetchall=True
        )
        return [dict(r) for r in results] if results else []
    
    def get_users_to_unban(self) -> list:
        """Получить пользователей для авторазбана"""
        results = self.execute(
            '''SELECT * FROM banned_users 
               WHERE is_unbanned = FALSE AND unban_time <= %s''',
            (datetime.now().isoformat(),),
            fetchall=True
        )
        return [dict(r) for r in results] if results else []

# Инициализация БД
try:
    db = Database()
    logger.info("✅ База данных инициализирована")
except Exception as e:
    logger.error(f"❌ Не удалось инициализировать базу данных: {e}")
    sys.exit(1)

# ========== ПАНЕЛЬ УПРАВЛЕНИЯ ==========
class ControlPanelView(discord.ui.View):
    """Панель управления ботом"""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="⚙️ Настройка сервера", style=discord.ButtonStyle.primary, custom_id="setup_btn", row=0)
    async def setup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка настройки сервера"""
        await interaction.response.defer(ephemeral=True)
        await setup_server_command(interaction)
    
    @discord.ui.button(label="➕ Добавить роль", style=discord.ButtonStyle.success, custom_id="add_role_btn", row=0)
    async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка добавления роли"""
        modal = AddRoleModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🗑️ Удалить роль", style=discord.ButtonStyle.danger, custom_id="remove_role_btn", row=0)
    async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка удаления роли"""
        await interaction.response.defer(ephemeral=True)
        await remove_tracked_role_command(interaction)
    
    @discord.ui.button(label="📋 Список ролей", style=discord.ButtonStyle.secondary, custom_id="list_roles_btn", row=0)
    async def list_roles_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка списка ролей"""
        await interaction.response.defer(ephemeral=True)
        await list_tracked_roles_command(interaction)
    
    @discord.ui.button(label="🔄 Синхронизация", style=discord.ButtonStyle.primary, custom_id="sync_btn", row=1)
    async def sync_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка синхронизации"""
        await interaction.response.defer(ephemeral=True)
        await sync_all_command(interaction)
    
    @discord.ui.button(label="📊 Статистика", style=discord.ButtonStyle.secondary, custom_id="stats_btn", row=1)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка статистики"""
        await interaction.response.defer(ephemeral=True)
        await server_stats_command(interaction)
    
    @discord.ui.button(label="🔓 Разбан", style=discord.ButtonStyle.success, custom_id="unban_btn", row=1)
    async def unban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка разбана"""
        modal = UnbanModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🏓 Пинг", style=discord.ButtonStyle.secondary, custom_id="ping_btn", row=1)
    async def ping_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Кнопка проверки пинга"""
        latency = round(bot.latency * 1000)
        
        embed = discord.Embed(
            title="🏓 Понг!",
            description=f"Задержка бота: **{latency}ms**",
            color=discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 300 else discord.Color.red()
        )
        
        if latency < 100:
            embed.add_field(name="Статус", value="✅ Отличное соединение", inline=False)
        elif latency < 300:
            embed.add_field(name="Статус", value="⚠️ Средняя задержка", inline=False)
        else:
            embed.add_field(name="Статус", value="❌ Высокая задержка", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class AddRoleModal(discord.ui.Modal, title="Добавить отслеживаемую роль"):
    """Модальное окно для добавления роли"""
    server_id = discord.ui.TextInput(
        label="ID сервера-источника",
        placeholder="Введите ID сервера, с которого отслеживать роль...",
        required=True,
        max_length=20
    )
    
    role_id = discord.ui.TextInput(
        label="ID роли на сервере-источнике",
        placeholder="Введите ID роли для отслеживания...",
        required=True,
        max_length=20
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await add_server_role_command(interaction, self.server_id.value, self.role_id.value)

class UnbanModal(discord.ui.Modal, title="Разблокировать пользователя"):
    """Модальное окно для разбана"""
    user_id = discord.ui.TextInput(
        label="ID пользователя",
        placeholder="Введите ID пользователя для разбана...",
        required=True,
        max_length=20
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await unban_user_command(interaction, self.user_id.value)

# ========== КЛАСС ДЛЯ НАСТРОЙКИ ДОСТУПА К КАНАЛАМ ==========
class ChannelPermissions:
    @staticmethod
    async def setup_channel_permissions(guild: discord.Guild, channel: discord.TextChannel, 
                                       admin_role1: discord.Role, admin_role2: discord.Role):
        """Настройка прав доступа для канала (изначально все закрыто)"""
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        for target, overwrite in overwrites.items():
            await channel.set_permissions(target, overwrite=overwrite)
    
    @staticmethod
    async def add_role_to_channels(guild: discord.Guild, role: discord.Role, settings: dict):
        """Добавить роль с нужными правами ко всем каналам"""
        if not settings:
            logger.warning(f"⚠️ Нет настроек сервера для настройки прав роли {role.name}")
            return
        
        configured_count = 0
        
        # 1. News - только читать
        if settings.get('news_channel_id'):
            news_channel = guild.get_channel(int(settings['news_channel_id']))
            if news_channel:
                await news_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True
                )
                configured_count += 1
        
        # 2. Flood - читать и писать
        if settings.get('flood_channel_id'):
            flood_channel = guild.get_channel(int(settings['flood_channel_id']))
            if flood_channel:
                await flood_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
                configured_count += 1
        
        # 3. Tags - только читать
        if settings.get('tags_channel_id'):
            tags_channel = guild.get_channel(int(settings['tags_channel_id']))
            if tags_channel:
                await tags_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True
                )
                configured_count += 1
        
        # 4. Media - читать и писать
        if settings.get('media_channel_id'):
            media_channel = guild.get_channel(int(settings['media_channel_id']))
            if media_channel:
                await media_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True
                )
                configured_count += 1
        
        # 5. Голосовые каналы - подключаться и говорить
        if settings.get('voice_channel_ids'):
            try:
                voice_ids = json.loads(settings['voice_channel_ids'])
                for voice_id in voice_ids:
                    voice_channel = guild.get_channel(int(voice_id))
                    if voice_channel:
                        await voice_channel.set_permissions(
                            role,
                            view_channel=True,
                            connect=True,
                            speak=True,
                            stream=True
                        )
                        configured_count += 1
            except Exception as e:
                logger.error(f"❌ Ошибка настройки голосовых каналов: {e}")
        
        return configured_count

# ========== КЛАСС ДЛЯ ЛОГИРОВАНИЯ ==========
class Logger:
    @staticmethod
    async def log_to_channel(guild: discord.Guild, message: str, color: discord.Color = discord.Color.blue()):
        """Отправить лог в канал logs"""
        try:
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            settings = db.get_server_settings(server_data['id'])
            
            logs_channel_id = settings.get('logs_channel_id')
            if not logs_channel_id:
                return
            
            logs_channel = guild.get_channel(int(logs_channel_id))
            if not logs_channel:
                return
            
            embed = discord.Embed(
                description=message,
                color=color,
                timestamp=datetime.now()
            )
            
            await logs_channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"❌ Ошибка логирования: {e}")
    
    @staticmethod
    async def log_command(interaction: discord.Interaction, command: str):
        """Логирование команды"""
        await Logger.log_to_channel(
            interaction.guild,
            f"**Команда выполнена**\n"
            f"• Команда: `{command}`\n"
            f"• Пользователь: {interaction.user.mention}\n"
            f"• Канал: {interaction.channel.mention}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
    
    @staticmethod
    async def log_role_action(guild: discord.Guild, user: discord.Member, action: str, role: discord.Role, reason: str = ""):
        """Логирование действий с ролями"""
        await Logger.log_to_channel(
            guild,
            f"**{action}**\n"
            f"• Пользователь: {user.mention}\n"
            f"• Роль: {role.mention}\n"
            f"• Причина: {reason}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.blue() if "Добавлена" in action else discord.Color.orange()
        )
    
    @staticmethod
    async def log_ban(guild: discord.Guild, user: discord.Member, reason: str, duration: int = 600):
        """Логирование бана"""
        unban_time = datetime.now() + timedelta(seconds=duration)
        await Logger.log_to_channel(
            guild,
            f"**🔨 Пользователь забанен**\n"
            f"• Пользователь: {user.mention}\n"
            f"• Причина: {reason}\n"
            f"• Длительность: 10 минут\n"
            f"• Разбан: {unban_time.strftime('%H:%M:%S')}",
            discord.Color.red()
        )
    
    @staticmethod
    async def log_unban(guild: discord.Guild, user_id: str, username: str, reason: str = ""):
        """Логирование разбана"""
        await Logger.log_to_channel(
            guild,
            f"**🔓 Пользователь разбанен**\n"
            f"• Пользователь: `{username}`\n"
            f"• Причина: {reason}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )

# ========== КЛАСС ДЛЯ РОЛЕЙ И БАНОВ ==========
class RoleMonitor:
    def __init__(self, bot):
        self.bot = bot
    
    async def check_user_roles(self, guild: discord.Guild, user_id: int):
        """Проверить роли пользователя на отслеживаемых серверах"""
        try:
            user = guild.get_member(user_id)
            if not user:
                return False, []
            
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            tracked_roles = db.get_tracked_roles(server_data['id'])
            
            servers_roles = {}
            for tracked in tracked_roles:
                server_id = tracked['source_server_id']
                if server_id not in servers_roles:
                    servers_roles[server_id] = []
                servers_roles[server_id].append(tracked)
            
            user_has_any_role = False
            found_roles = []
            
            for server_id, roles_list in servers_roles.items():
                server_has_role = False
                server_name = roles_list[0]['source_server_name'] if roles_list else "Неизвестно"
                
                for tracked in roles_list:
                    source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                    if not source_guild:
                        continue
                    
                    source_member = source_guild.get_member(user_id)
                    if source_member:
                        source_role = source_guild.get_role(int(tracked['source_role_id']))
                        if source_role and source_role in source_member.roles:
                            server_has_role = True
                            found_roles.append({
                                'role': tracked['source_role_name'],
                                'source_guild': server_name,
                                'target_role': tracked['target_role_name']
                            })
                            break
                
                if server_has_role:
                    user_has_any_role = True
            
            return user_has_any_role, found_roles
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки ролей: {e}")
            return False, []
    
    async def sync_user_roles(self, guild: discord.Guild, user_id: int):
        """Синхронизировать роли пользователя"""
        try:
            user = guild.get_member(user_id)
            if not user:
                return False
            
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            tracked_roles = db.get_tracked_roles(server_data['id'])
            
            servers_roles = {}
            for tracked in tracked_roles:
                server_id = tracked['source_server_id']
                if server_id not in servers_roles:
                    servers_roles[server_id] = []
                servers_roles[server_id].append(tracked)
            
            actions = []
            
            for server_id, roles_list in servers_roles.items():
                if not roles_list or not roles_list[0]['target_role_id']:
                    continue
                
                target_role = guild.get_role(int(roles_list[0]['target_role_id']))
                if not target_role:
                    continue
                
                has_any_source_role = False
                source_guild_name = "Неизвестно"
                
                for tracked in roles_list:
                    source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                    if not source_guild:
                        continue
                    
                    source_guild_name = source_guild.name
                    source_member = source_guild.get_member(user_id)
                    if source_member:
                        source_role = source_guild.get_role(int(tracked['source_role_id']))
                        if source_role and source_role in source_member.roles:
                            has_any_source_role = True
                            break
                
                if has_any_source_role and target_role not in user.roles:
                    await user.add_roles(target_role, reason=f"Имеет роль с {source_guild_name}")
                    await Logger.log_role_action(
                        guild, user, "✅ Роль добавлена", target_role, f"Имеет роль с {source_guild_name}"
                    )
                    actions.append(f"➕ Добавлена {target_role.name}")
                
                elif not has_any_source_role and target_role in user.roles:
                    await user.remove_roles(target_role, reason=f"Нет ролей с {source_guild_name}")
                    await Logger.log_role_action(
                        guild, user, "🗑️ Роль удалена", target_role, f"Нет ролей с {source_guild_name}"
                    )
                    actions.append(f"➖ Удалена {target_role.name}")
            
            user_has_any_role = False
            for server_id, roles_list in servers_roles.items():
                for tracked in roles_list:
                    source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                    if source_guild:
                        source_member = source_guild.get_member(user_id)
                        if source_member:
                            source_role = source_guild.get_role(int(tracked['source_role_id']))
                            if source_role and source_role in source_member.roles:
                                user_has_any_role = True
                                break
                if user_has_any_role:
                    break
            
            if not user_has_any_role and user_id not in [int(b['user_id']) for b in db.get_banned_users(server_data['id'])]:
                await self.ban_user(guild, user_id, user.display_name, "Отсутствие требуемых ролей")
                actions.append("🔨 Бан на 10 минут")
            
            if actions:
                await Logger.log_to_channel(
                    guild,
                    f"**🔍 Автопроверка пользователя**\n"
                    f"• Пользователь: {user.mention}\n"
                    f"• Статус: {'✅ Есть роли' if user_has_any_role else '❌ Нет ролей'}\n"
                    f"• Действия: {', '.join(actions)}",
                    discord.Color.purple()
                )
            
            return len(actions) > 0
            
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации: {e}")
            return False
    
    async def ban_user(self, guild: discord.Guild, user_id: int, username: str, reason: str):
        """Забанить пользователя на 10 минут"""
        try:
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            db.ban_user(server_data['id'], str(user_id), username, reason)
            
            user = guild.get_member(user_id)
            if user:
                await user.ban(reason=f"{reason} | Автобан на 10 минут", delete_message_days=0)
                await Logger.log_ban(guild, user, reason)
            else:
                user_obj = await self.bot.fetch_user(user_id)
                await guild.ban(user_obj, reason=f"{reason} | Автобан на 10 минут", delete_message_days=0)
                await Logger.log_ban(guild, user_obj, reason)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка бана: {e}")
            return False
    
    async def auto_unban_users(self):
        """Автоматический разбан пользователей"""
        try:
            users_to_unban = db.get_users_to_unban()
            
            for banned in users_to_unban:
                try:
                    server = self.bot.get_guild(int(banned['server_id']))
                    if server:
                        user = await self.bot.fetch_user(int(banned['user_id']))
                        await server.unban(user, reason="Автоматический разбан после 10 минут")
                        db.unban_user(banned['server_id'], banned['user_id'])
                        await Logger.log_unban(server, banned['user_id'], banned['username'], "Автоматический разбан")
                except:
                    pass
            
        except Exception as e:
            logger.error(f"❌ Ошибка в авторазбане: {e}")
    
    @tasks.loop(seconds=3)
    async def monitor_roles_task(self):
        """Фоновая задача для мониторинга ролей каждые 3 секунды"""
        try:
            await self.auto_unban_users()
            
            for guild in self.bot.guilds:
                try:
                    server_data = db.get_or_create_server(str(guild.id), guild.name)
                    tracked_roles = db.get_tracked_roles(server_data['id'])
                    
                    if not tracked_roles:
                        continue
                    
                    members = [m for m in guild.members if not m.bot]
                    
                    for member in members[:3]:
                        if not member.bot:
                            await self.sync_user_roles(guild, member.id)
                            await asyncio.sleep(0.1)
                            
                except Exception as e:
                    logger.error(f"❌ Ошибка мониторинга {guild.name}: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в задаче мониторинга: {e}")

# Инициализация монитора
role_monitor = RoleMonitor(bot)

# ========== КОМАНДЫ В ВИДЕ ФУНКЦИЙ ==========
async def setup_server_command(interaction: discord.Interaction):
    """Настройка сервера"""
    guild = interaction.guild
    
    try:
        await Logger.log_command(interaction, "Настройка сервера")
        
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        logger.info(f"🔧 Настройка сервера: {guild.name}")
        
        # 1. СОЗДАНИЕ АДМИНСКИХ РОЛЕЙ
        admin_role1 = await guild.create_role(
            name="Own",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.red(),
            reason="Настройка сервера"
        )
        
        admin_role2 = await guild.create_role(
            name="High",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.blue(),
            reason="Настройка сервера"
        )
        
        logger.info(f"✅ Созданы админские роли Own и High")
        
        # 2. СОЗДАНИЕ КАТЕГОРИЙ
        # Категория Main
        main_category = await guild.create_category(
            name="MAIN",
            reason="Категория для основных каналов"
        )
        
        # Категория High
        high_category = await guild.create_category(
            name="HIGH",
            reason="Категория для высокоуровневых каналов"
        )
        
        # Базовые права для категорий
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        await main_category.set_permissions(guild.default_role, view_channel=False)
        await high_category.set_permissions(guild.default_role, view_channel=False)
        
        # 3. СОЗДАНИЕ КАНАЛОВ В КАТЕГОРИИ MAIN
        # News
        news_channel = await main_category.create_text_channel(
            name="news",
            topic="📢 Новости сервера (только для чтения)",
            overwrites=base_overwrites
        )
        
        # Flood
        flood_channel = await main_category.create_text_channel(
            name="flood",
            topic="💬 Общий чат для всех",
            overwrites=base_overwrites
        )
        
        # Tags
        tags_channel = await main_category.create_text_channel(
            name="tags",
            topic="🏷️ Теги",
            overwrites=base_overwrites
        )
        
        # Media
        media_channel = await main_category.create_text_channel(
            name="media",
            topic="🖼️ Медиа-контент",
            overwrites=base_overwrites
        )
        
        # Голосовые каналы
        voice_channels = []
        for i in range(1, 5):
            voice_channel = await main_category.create_voice_channel(
                name=f"voice {i}",
                overwrites=base_overwrites
            )
            voice_channels.append(voice_channel)
        
        # 4. СОЗДАНИЕ КАНАЛОВ В КАТЕГОРИИ HIGH
        # Logs
        logs_channel = await high_category.create_text_channel(
            name="logs",
            topic="📊 Логи сервера",
            overwrites=base_overwrites
        )
        
        # High-flood
        high_flood_channel = await high_category.create_text_channel(
            name="high-flood",
            topic="🚨 Высокоуровневый чат",
            overwrites=base_overwrites
        )
        
        # High-voice (закрыт для всех кроме админов)
        high_voice_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        }
        
        high_voice_channel = await high_category.create_voice_channel(
            name="high-voice",
            overwrites=high_voice_overwrites
        )
        
        # 5. СОХРАНЕНИЕ В БАЗУ ДАННЫХ
        db.mark_server_setup(str(guild.id))
        
        settings = {
            'admin_role_1_id': str(admin_role1.id),
            'admin_role_2_id': str(admin_role2.id),
            'news_channel_id': str(news_channel.id),
            'flood_channel_id': str(flood_channel.id),
            'tags_channel_id': str(tags_channel.id),
            'media_channel_id': str(media_channel.id),
            'logs_channel_id': str(logs_channel.id),
            'high_flood_channel_id': str(high_flood_channel.id),
            'voice_channel_ids': [str(vc.id) for vc in voice_channels],
            'high_voice_channel_id': str(high_voice_channel.id),
            'main_category_id': str(main_category.id),
            'high_category_id': str(high_category.id)
        }
        db.save_server_settings(server_data['id'], settings)
        
        # 6. ОТЧЕТ
        embed = discord.Embed(
            title="🎉 Настройка сервера завершена!",
            description="Все каналы созданы и сгруппированы по категориям.",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="👑 Админские роли",
            value=f"{admin_role1.mention} (Own)\n{admin_role2.mention} (High)",
            inline=False
        )
        
        embed.add_field(
            name="📁 Категория MAIN",
            value=f"• {news_channel.mention} - news\n"
                  f"• {flood_channel.mention} - flood\n"
                  f"• {tags_channel.mention} - tags\n"
                  f"• {media_channel.mention} - media\n"
                  f"• Голосовые: {len(voice_channels)} канала",
            inline=True
        )
        
        embed.add_field(
            name="📁 Категория HIGH",
            value=f"• {logs_channel.mention} - logs\n"
                  f"• {high_flood_channel.mention} - high-flood\n"
                  f"• {high_voice_channel.mention} - high-voice",
            inline=True
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # 7. ЛОГИРОВАНИЕ
        await Logger.log_to_channel(
            guild,
            f"**🎉 Сервер настроен**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Категории: MAIN, HIGH\n"
            f"• Каналов: {len(voice_channels)+7}\n"
            f"• Роли: Own, High\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
        
        logger.info(f"✅ Сервер {guild.name} настроен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка настройки: {e}")
        await interaction.followup.send(
            f"❌ Ошибка при настройке сервера: {str(e)}",
            ephemeral=True
        )

async def add_server_role_command(interaction: discord.Interaction, source_server_id: str, source_role_id: str):
    """Добавить отслеживаемую роль"""
    guild = interaction.guild
    
    try:
        await Logger.log_command(interaction, "Добавить роль")
        
        if not source_server_id.isdigit() or not source_role_id.isdigit():
            await interaction.followup.send("❌ ID должны быть числовыми", ephemeral=True)
            return
        
        source_guild = bot.get_guild(int(source_server_id))
        if not source_guild:
            await interaction.followup.send("❌ Сервер-источник не найден", ephemeral=True)
            return
        
        source_role = source_guild.get_role(int(source_role_id))
        if not source_role:
            await interaction.followup.send("❌ Роль не найдена", ephemeral=True)
            return
        
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        tracked_roles = db.get_tracked_roles(server_data['id'])
        for role in tracked_roles:
            if role['source_server_id'] == source_server_id and role['source_role_id'] == source_role_id:
                await interaction.followup.send("❌ Роль уже отслеживается", ephemeral=True)
                return
        
        existing_target_role = None
        existing_roles_for_server = []
        
        for role in tracked_roles:
            if role['source_server_id'] == source_server_id:
                existing_roles_for_server.append(role)
                if role['target_role_id']:
                    target_role = guild.get_role(int(role['target_role_id']))
                    if target_role:
                        existing_target_role = target_role
                        break
        
        if existing_target_role:
            target_role = existing_target_role
            logger.info(f"♻️ Использую существующую роль {target_role.name}")
        else:
            role_name = source_guild.name[:32]
            target_role = await guild.create_role(
                name=role_name,
                permissions=discord.Permissions(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    connect=True,
                    speak=True
                ),
                color=discord.Color.random(),
                reason=f"Роль для отслеживания с сервера {source_guild.name}"
            )
            logger.info(f"✅ Создана новая роль {target_role.name}")
        
        settings = db.get_server_settings(server_data['id'])
        
        if not settings:
            await interaction.followup.send("❌ Сервер не настроен", ephemeral=True)
            return
        
        configured_count = 0
        if not existing_target_role:
            configured_count = await ChannelPermissions.add_role_to_channels(guild, target_role, settings)
        
        tracked_id = db.add_tracked_role(
            server_data['id'],
            source_server_id,
            source_role_id,
            source_guild.name,
            source_role.name
        )
        
        db.update_target_role(tracked_id, str(target_role.id), target_role.name)
        
        embed = discord.Embed(
            title="✅ Роль добавлена для отслеживания",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📡 Сервер-источник",
            value=f"**Имя:** {source_guild.name}\n**ID:** `{source_server_id}`",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Отслеживаемая роль",
            value=f"**Имя:** {source_role.name}\n**ID:** `{source_role_id}`",
            inline=False
        )
        
        if existing_target_role:
            embed.add_field(
                name="🔄 Используется существующая роль",
                value=f"{target_role.mention}\n**Всего ролей с этого сервера:** {len(existing_roles_for_server) + 1}",
                inline=False
            )
        else:
            embed.add_field(
                name="➕ Создана новая роль",
                value=f"{target_role.mention}",
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        if existing_target_role:
            action_type = "🔄 Добавлена дополнительная отслеживаемая роль"
        else:
            action_type = "📡 Добавлена первая отслеживаемая роль с сервера"
        
        await Logger.log_to_channel(
            guild,
            f"**{action_type}**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Сервер-источник: {source_guild.name}\n"
            f"• Отслеживаемая роль: {source_role.name}\n"
            f"• Используемая роль: {target_role.mention}",
            discord.Color.green() if not existing_target_role else discord.Color.blue()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления роли: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

async def remove_tracked_role_command(interaction: discord.Interaction):
    """Удалить отслеживаемую роль"""
    try:
        await Logger.log_command(interaction, "Удалить роль")
        
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            await interaction.followup.send("ℹ️ Нет отслеживаемых ролей", ephemeral=True)
            return
        
        servers_roles = {}
        for role in tracked_roles:
            server_id = role['source_server_id']
            if server_id not in servers_roles:
                servers_roles[server_id] = []
            servers_roles[server_id].append(role)
        
        embed = discord.Embed(
            title="🗑️ Удаление отслеживаемой роли",
            description="Выберите роль из списка ниже:",
            color=discord.Color.orange()
        )
        
        for server_id, roles_list in servers_roles.items():
            if roles_list:
                server_name = roles_list[0]['source_server_name'] or "Неизвестно"
                target_role = guild.get_role(int(roles_list[0]['target_role_id'])) if roles_list[0]['target_role_id'] else None
                roles_text = "\n".join([f"• {r['source_role_name']} (`{r['source_role_id']}`)" for r in roles_list])
                
                embed.add_field(
                    name=f"📡 {server_name}",
                    value=f"**Целевая роль:** {target_role.mention if target_role else '❌'}\n"
                          f"**Роли:**\n{roles_text}",
                    inline=False
                )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка удаления роли: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

async def list_tracked_roles_command(interaction: discord.Interaction):
    """Показать все отслеживаемые роли"""
    try:
        await Logger.log_command(interaction, "Список ролей")
        
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            await interaction.followup.send("ℹ️ Нет отслеживаемых ролей", ephemeral=True)
            return
        
        servers_roles = {}
        for role in tracked_roles:
            server_id = role['source_server_id']
            if server_id not in servers_roles:
                servers_roles[server_id] = []
            servers_roles[server_id].append(role)
        
        embed = discord.Embed(
            title=f"📋 Отслеживаемые роли ({len(tracked_roles)})",
            description="**Группировка: одна роль на сервер-источник**",
            color=discord.Color.purple()
        )
        
        for server_id, roles_list in servers_roles.items():
            target_role = None
            if roles_list[0]['target_role_id']:
                target_role = interaction.guild.get_role(int(roles_list[0]['target_role_id']))
            
            roles_text = []
            for role in roles_list:
                roles_text.append(f"• {role['source_role_name']} (`{role['source_role_id']}`)")
            
            value = f"**Сервер:** {roles_list[0]['source_server_name'] or 'Неизвестно'}\n"
            value += f"**Целевая роль:** {target_role.mention if target_role else '❌'}\n"
            value += f"**Всего ролей:** {len(roles_list)}\n"
            value += f"**Отслеживаемые роли:**\n" + "\n".join(roles_text)
            
            embed.add_field(
                name=f"📡 {target_role.name if target_role else 'Без имени'}",
                value=value,
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка ролей: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

async def sync_all_command(interaction: discord.Interaction):
    """Синхронизировать всех пользователей"""
    try:
        await Logger.log_command(interaction, "Синхронизация")
        
        guild = interaction.guild
        members = [m for m in guild.members if not m.bot]
        
        await interaction.followup.send(f"🔄 Начинаю синхронизацию {len(members)} пользователей...", ephemeral=True)
        
        processed = 0
        updated = 0
        banned = 0
        
        for member in members:
            processed += 1
            if await role_monitor.sync_user_roles(guild, member.id):
                updated += 1
            
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            banned_users = db.get_banned_users(server_data['id'])
            if member.id in [int(b['user_id']) for b in banned_users]:
                banned += 1
            
            if processed % 10 == 0:
                await interaction.edit_original_response(
                    content=f"🔄 Обработано {processed}/{len(members)} пользователей"
                )
            
            await asyncio.sleep(0.05)
        
        embed = discord.Embed(
            title="✅ Синхронизация завершена",
            description=f"**Обработано:** {processed} пользователей\n**Обновлено:** {updated}\n**Забанено:** {banned}",
            color=discord.Color.green()
        )
        
        await interaction.edit_original_response(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

async def unban_user_command(interaction: discord.Interaction, user_id: str):
    """Разбанить пользователя"""
    try:
        await Logger.log_command(interaction, "Разбан")
        
        if not user_id.isdigit():
            await interaction.followup.send("❌ ID должен быть числовым", ephemeral=True)
            return
        
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=f"Разбан администратором {interaction.user}")
        
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        db.unban_user(server_data['id'], user_id)
        
        embed = discord.Embed(
            title="🔓 Пользователь разблокирован",
            description=f"**Пользователь:** {user.name}\n**ID:** `{user_id}`",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        await Logger.log_unban(
            interaction.guild, 
            user_id, 
            user.name, 
            f"Разбан администратором {interaction.user}"
        )
        
    except discord.NotFound:
        await interaction.followup.send("❌ Пользователь не найден или не забанен", ephemeral=True)
    except Exception as e:
        logger.error(f"❌ Ошибка разбана: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

async def server_stats_command(interaction: discord.Interaction):
    """Показать статистику сервера"""
    try:
        await Logger.log_command(interaction, "Статистика")
        
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        banned_users = db.get_banned_users(server_data['id'])
        
        embed = discord.Embed(
            title=f"📊 Статистика {guild.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        total_members = len([m for m in guild.members if not m.bot])
        bot_count = len([m for m in guild.members if m.bot])
        
        embed.add_field(
            name="👥 Участники",
            value=f"Всего: {guild.member_count}\nПользователи: {total_members}\nБоты: {bot_count}",
            inline=True
        )
        
        embed.add_field(
            name="🔨 Баны",
            value=f"Активных: {len(banned_users)}\nАвторазбан: 10 мин",
            inline=True
        )
        
        servers_roles = {}
        for role in tracked_roles:
            server_id = role['source_server_id']
            if server_id not in servers_roles:
                servers_roles[server_id] = []
            servers_roles[server_id].append(role)
        
        embed.add_field(
            name=f"📡 Отслеживаемые роли",
            value=f"Серверов: {len(servers_roles)}\nРолей: {len(tracked_roles)}",
            inline=True
        )
        
        text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
        voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
        
        embed.add_field(
            name="💬 Каналы",
            value=f"Текстовые: {text_channels}\nГолосовые: {voice_channels}",
            inline=True
        )
        
        embed.add_field(
            name="👁️ Мониторинг",
            value="Статус: ✅\nПроверка: 3 сек",
            inline=True
        )
        
        settings = db.get_server_settings(server_data['id'])
        channel_status = "✅ Настроены" if settings else "❌ Не настроены"
        
        embed.add_field(
            name="🔧 Статус",
            value=f"Каналы: {channel_status}\nДоступ: через роли",
            inline=False
        )
        
        embed.set_footer(text=f"ID сервера: {guild.id}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        await interaction.followup.send(f"❌ Ошибка: {str(e)}", ephemeral=True)

# ========== КОМАНДА /SOUZ ==========
@bot.tree.command(name="souz", description="Панель управления ботом")
@app_commands.checks.has_permissions(administrator=True)
async def souz_command(interaction: discord.Interaction):
    """Главная панель управления ботом"""
    
    try:
        # Создаем основной embed
        embed = discord.Embed(
            title="🤝 **ДОБРО ПОЖАЛОВАТЬ В СОЮЗНЫЙ БОТ!**",
            description="Бот для управления доступом на основе ролей с других серверов",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="📋 **ОСНОВНЫЕ ФУНКЦИИ:**",
            value="• Автоматическая проверка ролей каждые 3 секунды\n"
                  "• Группировка ролей по серверам-источникам\n"
                  "• Автобан при отсутствии требуемых ролей (10 минут)\n"
                  "• Автоматическая настройка прав доступа к каналам\n"
                  "• Две категории каналов: MAIN и HIGH\n"
                  "• Подробное логирование всех действий",
            inline=False
        )
        
        embed.add_field(
            name="🔗 **ПРИГЛАСИТЬ БОТА НА СЕРВЕРЫ:**",
            value=f"[📋 Пригласить с правами администратора](https://discord.com/api/oauth2/authorize?client_id=1463842572832211061&permissions=8&scope=bot%20applications.commands)\n"
                  f"[👁️ Пригласить для просмотра ролей](https://discord.com/api/oauth2/authorize?client_id=1463842572832211061&permissions=268435456&scope=bot%20applications.commands)\n"
                  f"**ID бота:** `1463842572832211061`",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ **ЛОГИКА РАБОТЫ:**",
            value="• Одна роль на сервер-источник\n"
                  "• Все роли с одного сервера дают доступ к одной роли\n"
                  "• Условие доступа: ИЛИ (хотя бы одна роль из сервера)\n"
                  "• Пример: Роли 'Волк', 'Альфа', 'Вожак' с сервера 'Гильдия Волков' дают доступ к роли 'Гильдия Волков'",
            inline=False
        )
        
        embed.add_field(
            name="📁 **СТРУКТУРА КАНАЛОВ:**",
            value="**Категория MAIN:**\n"
                  "• news - только чтение\n"
                  "• flood - чтение/запись\n"
                  "• tags - только чтение\n"
                  "• media - чтение/запись + файлы\n"
                  "• voice 1-4 - голосовые каналы\n\n"
                  "**Категория HIGH:**\n"
                  "• logs - логи бота (только админы)\n"
                  "• high-flood - высокоуровневый чат (только админы)\n"
                  "• high-voice - голосовой канал (только админы)",
            inline=False
        )
        
        embed.add_field(
            name="👑 **АДМИНСКИЕ РОЛИ:**",
            value="• **Own** - владелец (красная роль)\n"
                  "• **High** - высокоуровневый администратор (синяя роль)\n"
                  "• Обе роли имеют полные права администратора",
            inline=False
        )
        
        embed.add_field(
            name="🚀 **БЫСТРЫЙ СТАРТ:**",
            value="1. Нажмите **'Настройка сервера'**\n"
                  "2. Добавьте роли через **'Добавить роль'**\n"
                  "3. Используйте **'Синхронизация'** для проверки всех пользователей\n"
                  "4. Наслаждайтесь автоматическим управлением доступом!",
            inline=False
        )
        
        embed.set_footer(text="Для получения помощи обращайтесь к разработчику")
        embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
        
        # Отправляем embed с панелью управления
        view = ControlPanelView()
        await interaction.response.send_message(embed=embed, view=view)
        
        # Логируем команду
        await Logger.log_command(interaction, "souz")
        
    except Exception as e:
        logger.error(f"❌ Ошибка команды souz: {e}")
        await interaction.response.send_message(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== СОБЫТИЯ БОТА ==========
@bot.event
async def on_ready():
    """Событие при запуске бота"""
    print(f'✅ Бот {bot.user} запущен!')
    print(f'🆔 ID бота: {bot.user.id}')
    print(f'📊 Серверов: {len(bot.guilds)}')
    
    try:
        synced = await bot.tree.sync()
        print(f'🔄 Синхронизировано команд: {len(synced)}')
    except Exception as e:
        print(f'❌ Ошибка синхронизации: {e}')
    
    role_monitor.monitor_roles_task.start()
    print('👁️ Мониторинг ролей запущен (каждые 3 секунды)')

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    print("=" * 50)
    print("🚀 Запуск Союзного Бота")
    print("=" * 50)
    print("📋 Основные функции:")
    print("  • Панель управления через /souz")
    print("  • 8 кнопок для управления")
    print("  • Две категории: MAIN и HIGH")
    print("  • Роли: Own и High")
    print("  • Группировка ролей по серверам")
    print("=" * 50)
    
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")