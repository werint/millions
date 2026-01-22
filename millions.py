import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import logging
import sys

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')  # Для Railway PostgreSQL

# Настройка интентов
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Создание бота
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== БАЗА ДАННЫХ (PostgreSQL) ==========
class Database:
    def __init__(self):
        self.conn = None
        self.connect()
        self.init_database()
    
    def connect(self):
        """Подключение к PostgreSQL Railway"""
        try:
            if DATABASE_URL:
                # Для Railway с DATABASE_URL
                self.conn = psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=RealDictCursor)
            else:
                # Для локальной разработки
                self.conn = psycopg2.connect(
                    host=os.getenv('PGHOST', 'localhost'),
                    database=os.getenv('PGDATABASE', 'railway'),
                    user=os.getenv('PGUSER', 'postgres'),
                    password=os.getenv('PGPASSWORD', ''),
                    port=os.getenv('PGPORT', 5432),
                    cursor_factory=RealDictCursor
                )
            logger.info("✅ Подключено к PostgreSQL")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
            sys.exit(1)
    
    def execute(self, query, params=None):
        """Выполнение SQL запроса"""
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(query, params or ())
                if query.strip().upper().startswith('SELECT'):
                    return cursor.fetchall()
                self.conn.commit()
                return cursor.rowcount
        except Exception as e:
            self.conn.rollback()
            logger.error(f"❌ Ошибка SQL: {e}")
            raise
    
    def init_database(self):
        """Инициализация таблиц БД"""
        logger.info("🔄 Инициализация таблиц БД...")
        
        # Таблица серверов
        self.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id SERIAL PRIMARY KEY,
                discord_id VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                is_setup BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица настроек сервера
        self.execute('''
            CREATE TABLE IF NOT EXISTS server_settings (
                id SERIAL PRIMARY KEY,
                server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
                admin_role_1_id VARCHAR(255),
                admin_role_2_id VARCHAR(255),
                news_channel_id VARCHAR(255),
                flood_channel_id VARCHAR(255),
                tags_channel_id VARCHAR(255),
                media_channel_id VARCHAR(255),
                logs_channel_id VARCHAR(255),
                high_flood_channel_id VARCHAR(255),
                voice_channel_ids TEXT[], -- Массив голосовых каналов
                UNIQUE(server_id)
            )
        ''')
        
        # Таблица отслеживаемых ролей (для команды /serv)
        self.execute('''
            CREATE TABLE IF NOT EXISTS tracked_roles (
                id SERIAL PRIMARY KEY,
                server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
                source_server_id VARCHAR(255) NOT NULL,
                source_server_name VARCHAR(255),
                source_role_id VARCHAR(255) NOT NULL,
                source_role_name VARCHAR(255),
                target_role_id VARCHAR(255),
                target_role_name VARCHAR(255),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, source_server_id, source_role_id)
            )
        ''')
        
        # Таблица пользователей с ролями
        self.execute('''
            CREATE TABLE IF NOT EXISTS user_roles (
                id SERIAL PRIMARY KEY,
                server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
                user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                tracked_role_id INTEGER REFERENCES tracked_roles(id) ON DELETE CASCADE,
                has_role BOOLEAN DEFAULT FALSE,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, user_id, tracked_role_id)
            )
        ''')
        
        # Таблица забаненных пользователей
        self.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                id SERIAL PRIMARY KEY,
                server_id INTEGER REFERENCES servers(id) ON DELETE CASCADE,
                user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL,
                ban_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                unban_time TIMESTAMP,
                ban_duration INTEGER DEFAULT 600,
                reason TEXT,
                is_unbanned BOOLEAN DEFAULT FALSE,
                UNIQUE(server_id, user_id)
            )
        ''')
        
        logger.info("✅ Таблицы БД созданы/проверены")
    
    # ========== МЕТОДЫ ДЛЯ СЕРВЕРОВ ==========
    
    def get_or_create_server(self, discord_id: str, name: str) -> dict:
        """Получить или создать сервер в БД"""
        result = self.execute(
            'SELECT * FROM servers WHERE discord_id = %s',
            (discord_id,)
        )
        
        if result:
            return result[0]
        
        self.execute(
            'INSERT INTO servers (discord_id, name) VALUES (%s, %s) RETURNING *',
            (discord_id, name)
        )
        result = self.execute(
            'SELECT * FROM servers WHERE discord_id = %s',
            (discord_id,)
        )
        return result[0] if result else None
    
    def mark_server_setup(self, discord_id: str):
        """Отметить сервер как настроенный"""
        self.execute(
            'UPDATE servers SET is_setup = TRUE WHERE discord_id = %s',
            (discord_id,)
        )
    
    # ========== МЕТОДЫ ДЛЯ НАСТРОЕК ==========
    
    def save_server_settings(self, server_id: int, settings: dict):
        """Сохранить настройки сервера"""
        self.execute('''
            INSERT INTO server_settings 
            (server_id, admin_role_1_id, admin_role_2_id, news_channel_id, 
             flood_channel_id, tags_channel_id, media_channel_id, 
             logs_channel_id, high_flood_channel_id, voice_channel_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (server_id) 
            DO UPDATE SET 
                admin_role_1_id = EXCLUDED.admin_role_1_id,
                admin_role_2_id = EXCLUDED.admin_role_2_id,
                news_channel_id = EXCLUDED.news_channel_id,
                flood_channel_id = EXCLUDED.flood_channel_id,
                tags_channel_id = EXCLUDED.tags_channel_id,
                media_channel_id = EXCLUDED.media_channel_id,
                logs_channel_id = EXCLUDED.logs_channel_id,
                high_flood_channel_id = EXCLUDED.high_flood_channel_id,
                voice_channel_ids = EXCLUDED.voice_channel_ids
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
            settings.get('voice_channel_ids', [])
        ))
    
    # ========== МЕТОДЫ ДЛЯ ОТСЛЕЖИВАЕМЫХ РОЛЕЙ ==========
    
    def add_tracked_role(self, server_id: int, source_server_id: str, source_role_id: str,
                        source_server_name: str = None, source_role_name: str = None) -> int:
        """Добавить отслеживаемую роль"""
        result = self.execute('''
            INSERT INTO tracked_roles 
            (server_id, source_server_id, source_role_id, source_server_name, source_role_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (server_id, source_server_id, source_role_id) 
            DO UPDATE SET 
                source_server_name = EXCLUDED.source_server_name,
                source_role_name = EXCLUDED.source_role_name,
                is_active = TRUE
            RETURNING id
        ''', (server_id, source_server_id, source_role_id, source_server_name, source_role_name))
        
        return result[0]['id'] if result else None
    
    def update_target_role(self, tracked_role_id: int, target_role_id: str, target_role_name: str):
        """Обновить целевую роль"""
        self.execute('''
            UPDATE tracked_roles 
            SET target_role_id = %s, target_role_name = %s 
            WHERE id = %s
        ''', (target_role_id, target_role_name, tracked_role_id))
    
    def get_tracked_roles(self, server_id: int) -> list:
        """Получить все отслеживаемые роли сервера"""
        return self.execute('''
            SELECT * FROM tracked_roles 
            WHERE server_id = %s AND is_active = TRUE
        ''', (server_id,))
    
    # ========== МЕТОДЫ ДЛЯ БАНОВ ==========
    
    def ban_user(self, server_id: int, user_id: str, username: str, reason: str = None) -> int:
        """Забанить пользователя"""
        unban_time = datetime.now() + timedelta(seconds=600)  # 10 минут
        result = self.execute('''
            INSERT INTO banned_users 
            (server_id, user_id, username, unban_time, reason)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (server_id, user_id) 
            DO UPDATE SET 
                username = EXCLUDED.username,
                ban_time = CURRENT_TIMESTAMP,
                unban_time = EXCLUDED.unban_time,
                reason = EXCLUDED.reason,
                is_unbanned = FALSE
            RETURNING id
        ''', (server_id, user_id, username, unban_time, reason))
        
        return result[0]['id'] if result else None
    
    def unban_user(self, server_id: int, user_id: str):
        """Разбанить пользователя"""
        self.execute('''
            UPDATE banned_users 
            SET is_unbanned = TRUE, unban_time = CURRENT_TIMESTAMP
            WHERE server_id = %s AND user_id = %s AND is_unbanned = FALSE
        ''', (server_id, user_id))
    
    def get_banned_users(self, server_id: int) -> list:
        """Получить забаненных пользователей"""
        return self.execute('''
            SELECT * FROM banned_users 
            WHERE server_id = %s AND is_unbanned = FALSE
        ''', (server_id,))
    
    def get_users_to_unban(self) -> list:
        """Получить пользователей для авторазбана"""
        return self.execute('''
            SELECT * FROM banned_users 
            WHERE is_unbanned = FALSE AND unban_time <= CURRENT_TIMESTAMP
        ''')
    
    def close(self):
        """Закрыть соединение с БД"""
        if self.conn:
            self.conn.close()
            logger.info("✅ Соединение с БД закрыто")

# Инициализация БД
db = Database()

# ========== КЛАСС ДЛЯ РОЛЕЙ И БАНОВ ==========
class RoleMonitor:
    def __init__(self, bot):
        self.bot = bot
        self.monitoring_tasks = {}
    
    async def check_user_roles(self, guild: discord.Guild, user_id: int):
        """Проверить роли пользователя на отслеживаемых серверах"""
        try:
            user = guild.get_member(user_id)
            if not user:
                return False, []
            
            # Получаем отслеживаемые роли для этого сервера
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            tracked_roles = db.get_tracked_roles(server_data['id'])
            
            user_has_any_role = False
            found_roles = []
            
            for tracked in tracked_roles:
                source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                if not source_guild:
                    continue
                
                source_member = source_guild.get_member(user_id)
                if source_member:
                    source_role = source_guild.get_role(int(tracked['source_role_id']))
                    if source_role and source_role in source_member.roles:
                        user_has_any_role = True
                        found_roles.append({
                            'role': tracked['target_role_name'] or tracked['source_role_name'],
                            'source_guild': source_guild.name
                        })
            
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
            
            user_has_any_role, found_roles = await self.check_user_roles(guild, user_id)
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            tracked_roles = db.get_tracked_roles(server_data['id'])
            
            actions = []
            
            for tracked in tracked_roles:
                if not tracked['target_role_id']:
                    continue
                
                target_role = guild.get_role(int(tracked['target_role_id']))
                if not target_role:
                    continue
                
                # Проверяем, есть ли у пользователя исходная роль
                source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                has_source_role = False
                
                if source_guild:
                    source_member = source_guild.get_member(user_id)
                    if source_member:
                        source_role = source_guild.get_role(int(tracked['source_role_id']))
                        has_source_role = source_role and source_role in source_member.roles
                
                # Синхронизируем
                if has_source_role and target_role not in user.roles:
                    await user.add_roles(target_role, reason="Синхронизация ролей")
                    actions.append(f"➕ Добавлена {target_role.name}")
                
                elif not has_source_role and target_role in user.roles:
                    await user.remove_roles(target_role, reason="Синхронизация ролей")
                    actions.append(f"➖ Удалена {target_role.name}")
            
            # Если нет ни одной роли - бан на 10 минут
            if not user_has_any_role and user_id not in [int(b['user_id']) for b in db.get_banned_users(server_data['id'])]:
                await self.ban_user(guild, user_id, user.display_name, "Отсутствие требуемых ролей")
                actions.append("🔨 Бан на 10 минут")
            
            return len(actions) > 0
            
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации: {e}")
            return False
    
    async def ban_user(self, guild: discord.Guild, user_id: int, username: str, reason: str):
        """Забанить пользователя на 10 минут"""
        try:
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            ban_id = db.ban_user(server_data['id'], str(user_id), username, reason)
            
            # Баним на сервере
            user = guild.get_member(user_id)
            if user:
                await user.ban(reason=f"{reason} | Автобан на 10 минут", delete_message_days=0)
            
            logger.info(f"🔨 Пользователь {username} забанен на 10 минут")
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
                        await server.unban(user, reason="Автоматический разбан")
                        db.unban_user(banned['server_id'], banned['user_id'])
                        logger.info(f"🔓 Авторазбан {banned['username']}")
                except Exception as e:
                    logger.error(f"❌ Ошибка авторазбана: {e}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка в авторазбане: {e}")
    
    @tasks.loop(minutes=1)
    async def monitor_roles_task(self):
        """Фоновая задача для мониторинга ролей"""
        await self.auto_unban_users()
        
        for guild in self.bot.guilds:
            try:
                server_data = db.get_or_create_server(str(guild.id), guild.name)
                tracked_roles = db.get_tracked_roles(server_data['id'])
                
                if not tracked_roles:
                    continue
                
                for member in guild.members:
                    if not member.bot:
                        await self.sync_user_roles(guild, member.id)
                        await asyncio.sleep(0.1)  # Задержка чтобы не перегружать
                        
            except Exception as e:
                logger.error(f"❌ Ошибка мониторинга {guild.name}: {e}")

# Инициализация монитора
role_monitor = RoleMonitor(bot)

# ========== КОМАНДЫ БОТА ==========
@bot.event
async def on_ready():
    """Событие при запуске бота"""
    print(f'✅ Бот {bot.user} запущен!')
    print(f'🆔 ID бота: {bot.user.id}')
    
    # Синхронизация команд
    try:
        synced = await bot.tree.sync()
        print(f'🔄 Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'❌ Ошибка синхронизации: {e}')
    
    # Запуск мониторинга
    role_monitor.monitor_roles_task.start()
    print('👁️ Мониторинг ролей запущен')

# ========== КОМАНДА /SETT ==========
@bot.tree.command(name="sett", description="Настройка сервера: создание каналов и админских ролей")
@app_commands.checks.has_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    """Создает структуру сервера без обычных ролей"""
    
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    try:
        # Сохраняем сервер в БД
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        logger.info(f"🔧 Настройка сервера: {guild.name}")
        
        # 1. СОЗДАНИЕ АДМИНСКИХ РОЛЕЙ (только 2 админские, без обычных!)
        admin_role1 = await guild.create_role(
            name="Админ-1",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.red(),
            reason="Настройка сервера через /sett"
        )
        
        admin_role2 = await guild.create_role(
            name="Админ-2",
            permissions=discord.Permissions(administrator=True),
            color=discord.Color.blue(),
            reason="Настройка сервера через /sett"
        )
        
        logger.info(f"✅ Созданы админские роли")
        
        # 2. БАЗОВЫЕ ПРАВА ДОСТУПА (только для админов)
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        # 3. СОЗДАНИЕ ТЕКСТОВЫХ КАНАЛОВ
        
        # 1.1 News - видят все, пишут только админы
        news_overwrites = base_overwrites.copy()
        news_overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False
        )
        
        news_channel = await guild.create_text_channel(
            name="news",
            topic="📢 Новости сервера (только для чтения)",
            overwrites=news_overwrites,
            reason="Канал News из команды /sett"
        )
        
        # 1.2 Flood - видят и пишут все
        flood_overwrites = base_overwrites.copy()
        flood_overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True
        )
        
        flood_channel = await guild.create_text_channel(
            name="flood",
            topic="💬 Общий чат для всех",
            overwrites=flood_overwrites,
            reason="Канал Flood из команды /sett"
        )
        
        # 1.3 Tags - админы пишут, обычные только смотрят
        tags_overwrites = base_overwrites.copy()
        tags_overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False
        )
        
        tags_channel = await guild.create_text_channel(
            name="tags",
            topic="🏷️ Теги (только для админов)",
            overwrites=tags_overwrites,
            reason="Канал Tags из команды /sett"
        )
        
        # 1.4 Media - все могут писать
        media_overwrites = base_overwrites.copy()
        media_overwrites[guild.default_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True
        )
        
        media_channel = await guild.create_text_channel(
            name="media",
            topic="🖼️ Медиа-контент",
            overwrites=media_overwrites,
            reason="Канал Media из команды /sett"
        )
        
        logger.info(f"✅ Созданы публичные текстовые каналы")
        
        # 4. ЗАКРЫТЫЕ КАНАЛЫ (ТОЛЬКО ДЛЯ АДМИНОВ)
        
        # 1.5 Logs - только для админов
        logs_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        logs_channel = await guild.create_text_channel(
            name="logs",
            topic="📊 Логи сервера (только для админов)",
            overwrites=logs_overwrites,
            reason="Канал Logs из команды /sett"
        )
        
        # 1.6 High-flood - только для админов
        high_flood_channel = await guild.create_text_channel(
            name="high-flood",
            topic="🚨 Высокоуровневый чат (только для админов)",
            overwrites=logs_overwrites,
            reason="Канал High-flood из команды /sett"
        )
        
        logger.info(f"✅ Созданы закрытые каналы")
        
        # 5. ГОЛОСОВЫЕ КАНАЛЫ (4 штуки)
        voice_overwrites = base_overwrites.copy()
        voice_overwrites[guild.default_role] = discord.PermissionOverwrite(
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
        
        logger.info(f"✅ Созданы голосовые каналы")
        
        # 6. СОХРАНЕНИЕ В БАЗУ ДАННЫХ
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
            'voice_channel_ids': [str(vc.id) for vc in voice_channels]
        }
        db.save_server_settings(server_data['id'], settings)
        
        # 7. ОТЧЕТ
        embed = discord.Embed(
            title="🎉 Настройка сервера завершена!",
            description="Структура сервера создана. Теперь используйте `/serv` для добавления ролей.",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="👑 Админские роли",
            value=f"{admin_role1.mention}\n{admin_role2.mention}",
            inline=False
        )
        
        embed.add_field(
            name="💬 Текстовые каналы",
            value=f"{news_channel.mention} - все видят, пишут админы\n"
                  f"{flood_channel.mention} - все видят и пишут\n"
                  f"{tags_channel.mention} - админы пишут, остальные читают\n"
                  f"{media_channel.mention} - все могут писать и отправлять файлы",
            inline=False
        )
        
        embed.add_field(
            name="🔒 Закрытые каналы",
            value=f"{logs_channel.mention} - только для админов\n"
                  f"{high_flood_channel.mention} - только для админов",
            inline=False
        )
        
        embed.add_field(
            name="🎤 Голосовые каналы",
            value="\n".join([vc.mention for vc in voice_channels]),
            inline=False
        )
        
        embed.set_footer(text=f"Настроено пользователем {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"✅ Сервер {guild.name} настроен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка настройки: {e}")
        await interaction.followup.send(
            f"❌ Ошибка при настройке сервера: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /SERV ==========
@bot.tree.command(name="serv", description="Добавить отслеживаемую роль с другого сервера")
@app_commands.describe(
    source_server_id="ID сервера-источника",
    source_role_id="ID роли на сервере-источнике"
)
@app_commands.checks.has_permissions(administrator=True)
async def add_server_role(interaction: discord.Interaction, 
                         source_server_id: str, 
                         source_role_id: str):
    """Добавляет отслеживаемую роль и создает соответствующую роль на текущем сервере"""
    
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    try:
        # Проверяем валидность ID
        if not source_server_id.isdigit() or not source_role_id.isdigit():
            await interaction.followup.send(
                "❌ ID сервера и роли должны быть числовыми",
                ephemeral=True
            )
            return
        
        # Получаем информацию о сервере-источнике
        source_guild = bot.get_guild(int(source_server_id))
        if not source_guild:
            await interaction.followup.send(
                "❌ Не удалось найти сервер-источник. Проверьте ID и убедитесь, что бот находится на этом сервере.",
                ephemeral=True
            )
            return
        
        # Получаем информацию о роли
        source_role = source_guild.get_role(int(source_role_id))
        if not source_role:
            await interaction.followup.send(
                "❌ Не удалось найти роль на сервере-источнике",
                ephemeral=True
            )
            return
        
        # Сохраняем сервер в БД
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        # Проверяем, не добавлена ли уже эта роль
        existing = db.execute(
            'SELECT * FROM tracked_roles WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s',
            (server_data['id'], source_server_id, source_role_id)
        )
        
        if existing:
            await interaction.followup.send(
                f"❌ Роль уже отслеживается!",
                ephemeral=True
            )
            return
        
        # 1. СОЗДАЕМ РОЛЬ НА ТЕКУЩЕМ СЕРВЕРЕ
        # Имя роли = имя сервера-источника
        role_name = source_guild.name[:30]  # Ограничение Discord
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
        
        logger.info(f"✅ Создана роль {target_role.name} для сервера {source_guild.name}")
        
        # 2. НАСТРАИВАЕМ ДОСТУП К СУЩЕСТВУЮЩИМ КАНАЛАМ
        # Получаем настройки сервера
        settings = db.execute(
            'SELECT * FROM server_settings WHERE server_id = %s',
            (server_data['id'],)
        )
        
        if settings:
            settings = settings[0]
            
            # Каналы, куда даем доступ (кроме закрытых админских)
            channel_ids = [
                settings.get('news_channel_id'),
                settings.get('flood_channel_id'),
                settings.get('tags_channel_id'),
                settings.get('media_channel_id')
            ]
            
            # Добавляем голосовые каналы
            if settings.get('voice_channel_ids'):
                channel_ids.extend(settings['voice_channel_ids'])
            
            for channel_id in channel_ids:
                if channel_id:
                    try:
                        channel = guild.get_channel(int(channel_id))
                        if channel:
                            # Разрешаем просмотр и отправку сообщений
                            await channel.set_permissions(
                                target_role,
                                view_channel=True,
                                send_messages=True,
                                read_message_history=True
                            )
                    except:
                        pass
        
        # 3. СОХРАНЯЕМ В БАЗУ ДАННЫХ
        tracked_id = db.add_tracked_role(
            server_data['id'],
            source_server_id,
            source_role_id,
            source_guild.name,
            source_role.name
        )
        
        db.update_target_role(tracked_id, str(target_role.id), target_role.name)
        
        # 4. ОТЧЕТ
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
        
        embed.add_field(
            name="➕ Созданная роль",
            value=f"{target_role.mention}\n**Имя:** {target_role.name}\n**ID:** `{target_role.id}`",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Настроено",
            value=f"• Доступ к публичным каналам\n• Мониторинг ролей включен\n• Автобан при потере роли",
            inline=False
        )
        
        embed.set_footer(text="Теперь бот будет отслеживать эту роль и выдавать/убирать доступ автоматически")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # 5. ПРОВЕРЯЕМ СРАЗУ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
        await interaction.followup.send(
            "🔄 Начинаю проверку всех пользователей...",
            ephemeral=True
        )
        
        checked = 0
        updated = 0
        
        for member in guild.members:
            if not member.bot:
                checked += 1
                if await role_monitor.sync_user_roles(guild, member.id):
                    updated += 1
                await asyncio.sleep(0.1)
        
        await interaction.followup.send(
            f"✅ Проверено {checked} пользователей, обновлено {updated}",
            ephemeral=True
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка команды /serv: {e}")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /STATS ==========
@bot.tree.command(name="stats", description="Статистика сервера и отслеживаемых ролей")
@app_commands.checks.has_permissions(administrator=True)
async def server_stats(interaction: discord.Interaction):
    """Показать статистику сервера"""
    
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    try:
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        banned_users = db.get_banned_users(server_data['id'])
        
        embed = discord.Embed(
            title=f"📊 Статистика {guild.name}",
            color=discord.Color.blue()
        )
        
        # Основная информация
        embed.add_field(
            name="👥 Участники",
            value=f"Всего: {guild.member_count}\nБоты: {len([m for m in guild.members if m.bot])}",
            inline=True
        )
        
        embed.add_field(
            name="🔨 Баны",
            value=f"Активных: {len(banned_users)}\nАвторазбан: через 10 мин",
            inline=True
        )
        
        # Отслеживаемые роли
        if tracked_roles:
            roles_text = ""
            for role in tracked_roles[:5]:  # Показываем первые 5
                roles_text += f"• {role['target_role_name'] or 'Без имени'}\n"
            
            if len(tracked_roles) > 5:
                roles_text += f"... и ещё {len(tracked_roles) - 5}"
            
            embed.add_field(
                name=f"📡 Отслеживаемые роли ({len(tracked_roles)})",
                value=roles_text or "Нет",
                inline=False
            )
        else:
            embed.add_field(
                name="📡 Отслеживаемые роли",
                value="Нет отслеживаемых ролей\nИспользуйте `/serv` для добавления",
                inline=False
            )
        
        embed.set_footer(text=f"ID сервера: {guild.id}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /CHECK ==========
@bot.tree.command(name="check", description="Проверить конкретного пользователя")
@app_commands.describe(
    user="Пользователь для проверки"
)
@app_commands.checks.has_permissions(administrator=True)
async def check_user(interaction: discord.Interaction, user: discord.Member):
    """Проверить роли пользователя"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        has_role, found_roles = await role_monitor.check_user_roles(interaction.guild, user.id)
        
        embed = discord.Embed(
            title=f"🔍 Проверка {user.display_name}",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="👤 Пользователь",
            value=f"{user.mention}\nID: `{user.id}`",
            inline=False
        )
        
        if has_role:
            embed.add_field(
                name="✅ Есть доступ",
                value="Пользователь имеет хотя бы одну отслеживаемую роль",
                inline=False
            )
            
            if found_roles:
                roles_text = "\n".join([f"• {r['role']} ({r['source_guild']})" for r in found_roles])
                embed.add_field(
                    name="📋 Найденные роли",
                    value=roles_text,
                    inline=False
                )
        else:
            embed.add_field(
                name="❌ Нет доступа",
                value="Пользователь не имеет отслеживаемых ролей\nМожет быть забанен автоматически",
                inline=False
            )
        
        # Проверяем статус бана
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        banned = db.execute(
            'SELECT * FROM banned_users WHERE server_id = %s AND user_id = %s AND is_unbanned = FALSE',
            (server_data['id'], str(user.id))
        )
        
        if banned:
            ban_info = banned[0]
            ban_time = ban_info['ban_time']
            unban_time = ban_info['unban_time']
            
            if unban_time:
                time_left = unban_time - datetime.now()
                if time_left.total_seconds() > 0:
                    minutes = int(time_left.total_seconds() // 60)
                    seconds = int(time_left.total_seconds() % 60)
                    embed.add_field(
                        name="🔨 Забанен",
                        value=f"До разбана: {minutes}м {seconds}с\nПричина: {ban_info['reason']}",
                        inline=False
                    )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки: {e}")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /UNBAN ==========
@bot.tree.command(name="unban", description="Разблокировать пользователя")
@app_commands.describe(
    user_id="ID пользователя для разбана"
)
@app_commands.checks.has_permissions(administrator=True)
async def unban_user(interaction: discord.Interaction, user_id: str):
    """Разбанить пользователя"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Проверяем валидность ID
        if not user_id.isdigit():
            await interaction.followup.send(
                "❌ ID пользователя должен быть числовым",
                ephemeral=True
            )
            return
        
        # Получаем пользователя
        user = await bot.fetch_user(int(user_id))
        
        # Разбаниваем на сервере
        await interaction.guild.unban(user, reason=f"Разбан администратором {interaction.user}")
        
        # Обновляем в БД
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        db.unban_user(server_data['id'], user_id)
        
        embed = discord.Embed(
            title="🔓 Пользователь разблокирован",
            description=f"**Пользователь:** {user.name}\n**ID:** `{user_id}`\n**Администратор:** {interaction.user.mention}",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except discord.NotFound:
        await interaction.followup.send(
            "❌ Пользователь не найден или не забанен",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"❌ Ошибка разбана: {e}")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /LIST_ROLES ==========
@bot.tree.command(name="list_roles", description="Список всех отслеживаемых ролей")
@app_commands.checks.has_permissions(administrator=True)
async def list_tracked_roles(interaction: discord.Interaction):
    """Показать все отслеживаемые роли"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            await interaction.followup.send(
                "ℹ️ Нет отслеживаемых ролей. Используйте `/serv` для добавления.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title=f"📋 Отслеживаемые роли ({len(tracked_roles)})",
            color=discord.Color.purple()
        )
        
        for role in tracked_roles:
            target_role = interaction.guild.get_role(int(role['target_role_id'])) if role['target_role_id'] else None
            
            value = f"**Сервер:** {role['source_server_name'] or 'Неизвестно'}\n"
            value += f"**Роль:** {role['source_role_name'] or 'Неизвестно'}\n"
            value += f"**ID роли:** `{role['source_role_id']}`\n"
            value += f"**Целевая роль:** {target_role.mention if target_role else 'Не найдена'}"
            
            embed.add_field(
                name=f"🎯 {role['target_role_name'] or 'Без имени'}",
                value=value,
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка ролей: {e}")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    print("🚀 Запуск Discord бота с PostgreSQL...")
    print(f"📦 Версия discord.py: {discord.__version__}")
    
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        # Закрываем соединение с БД
        db.close()