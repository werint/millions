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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("❌ ОШИБКА: DISCORD_TOKEN не найден!")
    print("💡 Установите переменную в настройках Railway:")
    print("   DISCORD_TOKEN=ваш_токен_бота")
    sys.exit(1)

# Настройка интентов
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Создание бота
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== БАЗА ДАННЫХ (PostgreSQL для Railway) ==========
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    logger.error("❌ psycopg2 не установлен!")
    POSTGRES_AVAILABLE = False

class Database:
    def __init__(self):
        self.conn = None
        self.connect()
        if self.conn:
            self.init_database()
    
    def connect(self):
        """Подключение к PostgreSQL Railway"""
        if not POSTGRES_AVAILABLE:
            logger.error("❌ psycopg2 не установлен. Запустите: pip install psycopg2-binary")
            sys.exit(1)
        
        try:
            # Получаем DATABASE_URL из переменных окружения Railway
            DATABASE_URL = os.getenv('DATABASE_URL')
            
            if DATABASE_URL:
                # Railway предоставляет DATABASE_URL в формате:
                # postgresql://username:password@host:port/database
                # Нужно преобразовать для psycopg2
                if DATABASE_URL.startswith('postgresql://'):
                    # Заменяем на формат psycopg2
                    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgres://')
                
                logger.info(f"🔗 Подключаюсь к PostgreSQL через DATABASE_URL")
                self.conn = psycopg2.connect(
                    DATABASE_URL,
                    sslmode='require',
                    cursor_factory=RealDictCursor
                )
            else:
                # Альтернативные переменные окружения
                db_config = {
                    'host': os.getenv('PGHOST'),
                    'database': os.getenv('PGDATABASE'),
                    'user': os.getenv('PGUSER'),
                    'password': os.getenv('PGPASSWORD'),
                    'port': os.getenv('PGPORT', 5432)
                }
                
                # Проверяем, все ли переменные есть
                if all(db_config.values()):
                    logger.info(f"🔗 Подключаюсь к PostgreSQL: {db_config['host']}:{db_config['port']}")
                    self.conn = psycopg2.connect(
                        host=db_config['host'],
                        database=db_config['database'],
                        user=db_config['user'],
                        password=db_config['password'],
                        port=db_config['port'],
                        cursor_factory=RealDictCursor
                    )
                else:
                    logger.error("❌ Не найдены переменные PostgreSQL!")
                    logger.error("💡 На Railway добавьте PostgreSQL через Add Plugin")
                    logger.error("💡 Railway автоматически создаст DATABASE_URL")
                    sys.exit(1)
            
            logger.info("✅ Успешное подключение к PostgreSQL")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
            
            # Подсказки для решения проблемы
            logger.info("💡 Решение проблем:")
            logger.info("1. На Railway добавьте PostgreSQL через 'Add Plugin'")
            logger.info("2. Railway автоматически создаст DATABASE_URL")
            logger.info("3. Или укажите переменные вручную:")
            logger.info("   PGHOST, PGDATABASE, PGUSER, PGPASSWORD, PGPORT")
            logger.info("4. Проверьте что psycopg2-binary установлен")
            
            # Запрашиваем конфигурацию у пользователя
            self.setup_database_manually()
            return False
    
    def setup_database_manually(self):
        """Ручная настройка базы данных"""
        logger.info("🔄 Пытаюсь использовать SQLite как временное решение...")
        
        try:
            import sqlite3
            self.use_sqlite = True
            self.db_name = 'bot_database.db'
            
            # Создаем SQLite соединение
            self.conn = sqlite3.connect(self.db_name)
            self.conn.row_factory = sqlite3.Row
            
            logger.info(f"✅ Использую SQLite базу: {self.db_name}")
            logger.info("⚠️ ВНИМАНИЕ: SQLite не поддерживает многопользовательский доступ")
            logger.info("💡 Для продакшена используйте PostgreSQL на Railway")
            
            return True
        except Exception as e:
            logger.error(f"❌ Не удалось создать SQLite базу: {e}")
            sys.exit(1)
    
    def execute(self, query, params=None, fetchone=False, fetchall=False):
        """Выполнение SQL запроса"""
        try:
            cursor = self.conn.cursor()
            
            # Адаптируем запрос для SQLite если нужно
            if hasattr(self, 'use_sqlite') and self.use_sqlite:
                query = query.replace('%s', '?')
                query = query.replace('SERIAL', 'INTEGER')
                query = query.replace('VARCHAR', 'TEXT')
                query = query.replace('BOOLEAN', 'INTEGER')
                query = query.replace('TRUE', '1')
                query = query.replace('FALSE', '0')
                query = query.replace('TIMESTAMP DEFAULT CURRENT_TIMESTAMP', 'TIMESTAMP')
                query = query.replace('ON CONFLICT DO UPDATE', 'ON CONFLICT REPLACE')
                query = query.replace('EXCLUDED.', 'excluded.')
            
            cursor.execute(query, params or ())
            
            if fetchone:
                result = cursor.fetchone()
            elif fetchall:
                result = cursor.fetchall()
            else:
                result = cursor.rowcount
            
            if not hasattr(self, 'use_sqlite') or not self.use_sqlite:
                self.conn.commit()
            else:
                self.conn.commit()
            
            cursor.close()
            return result
        except Exception as e:
            if not hasattr(self, 'use_sqlite') or not self.use_sqlite:
                self.conn.rollback()
            logger.error(f"❌ Ошибка SQL: {e}")
            logger.error(f"Запрос: {query[:100]}...")
            raise
    
    def init_database(self):
        """Инициализация таблиц БД"""
        logger.info("🔄 Создание таблиц в базе данных...")
        
        try:
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
                    UNIQUE(server_id)
                )
            ''')
            
            # Таблица отслеживаемых ролей
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(server_id, source_server_id, source_role_id)
                )
            ''')
            
            # Таблица пользователей с ролями
            self.execute('''
                CREATE TABLE IF NOT EXISTS user_roles (
                    id SERIAL PRIMARY KEY,
                    server_id INTEGER NOT NULL,
                    user_id VARCHAR(255) NOT NULL,
                    username VARCHAR(255),
                    tracked_role_id INTEGER NOT NULL,
                    has_role BOOLEAN DEFAULT FALSE,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(server_id, user_id, tracked_role_id)
                )
            ''')
            
            # Таблица забаненных пользователей
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
                    is_unbanned BOOLEAN DEFAULT FALSE,
                    UNIQUE(server_id, user_id)
                )
            ''')
            
            logger.info("✅ Таблицы базы данных успешно созданы/проверены")
            
            # Проверяем соединение
            test_result = self.execute('SELECT 1 as test', fetchone=True)
            if test_result:
                logger.info(f"✅ Тест подключения к БД пройден")
            else:
                logger.error("❌ Тест подключения к БД не пройден")
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка инициализации БД: {e}")
            raise
    
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
                   VALUES (%s, %s)
                   ON CONFLICT (discord_id) DO NOTHING''',
                (discord_id, name)
            )
        except Exception as e:
            # Для SQLite другой синтаксис
            if 'DO NOTHING' in str(e):
                self.execute(
                    '''INSERT OR IGNORE INTO servers (discord_id, name) 
                       VALUES (%s, %s)''',
                    (discord_id, name)
                )
            else:
                raise
        
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
        
        try:
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
                voice_channel_ids
            ))
        except Exception as e:
            # Для SQLite
            if 'EXCLUDED' in str(e):
                self.execute('''
                    INSERT OR REPLACE INTO server_settings 
                    (server_id, admin_role_1_id, admin_role_2_id, news_channel_id, 
                     flood_channel_id, tags_channel_id, media_channel_id, 
                     logs_channel_id, high_flood_channel_id, voice_channel_ids)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    voice_channel_ids
                ))
            else:
                raise
    
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
        # Сначала пробуем найти существующую
        result = self.execute(
            '''SELECT id FROM tracked_roles 
               WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s''',
            (server_id, source_server_id, source_role_id),
            fetchone=True
        )
        
        if result:
            # Активируем существующую
            self.execute(
                'UPDATE tracked_roles SET is_active = TRUE WHERE id = %s',
                (result['id'],)
            )
            return result['id']
        
        # Создаем новую
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
        
        try:
            self.execute('''
                INSERT INTO banned_users 
                (server_id, user_id, username, unban_time, reason)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (server_id, user_id) 
                DO UPDATE SET 
                    username = EXCLUDED.username,
                    unban_time = EXCLUDED.unban_time,
                    reason = EXCLUDED.reason,
                    ban_time = CURRENT_TIMESTAMP,
                    is_unbanned = FALSE
            ''', (server_id, user_id, username, unban_time.isoformat(), reason))
        except Exception as e:
            # Для SQLite
            if 'EXCLUDED' in str(e):
                self.execute('''
                    INSERT OR REPLACE INTO banned_users 
                    (server_id, user_id, username, unban_time, reason)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (server_id, user_id, username, unban_time.isoformat(), reason))
            else:
                raise
        
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

# ========== КОМАНДА УДАЛЕНИЯ РОЛЕЙ ==========
class DeleteRoleView(discord.ui.View):
    """Представление для удаления отслеживаемых ролей"""
    def __init__(self, guild: discord.Guild, tracked_roles: list):
        super().__init__(timeout=60)
        self.guild = guild
        self.tracked_roles = tracked_roles
        
        # Добавляем выпадающий список с ролями
        self.add_item(RoleSelect(tracked_roles))

class RoleSelect(discord.ui.Select):
    """Выпадающий список для выбора роли"""
    def __init__(self, tracked_roles: list):
        options = []
        
        for role in tracked_roles:
            option = discord.SelectOption(
                label=role['target_role_name'] or role['source_server_name'],
                value=str(role['id']),
                description=f"Сервер: {role['source_server_name']} | Роль: {role['source_role_name']}"
            )
            options.append(option)
        
        super().__init__(
            placeholder="Выберите роль для удаления...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_id = int(self.values[0])
        
        # Находим выбранную роль
        selected_role = None
        for role in self.view.tracked_roles:
            if role['id'] == selected_id:
                selected_role = role
                break
        
        if not selected_role:
            await interaction.response.send_message(
                "❌ Роль не найдена!",
                ephemeral=True
            )
            return
        
        # Получаем объект роли на сервере
        target_role = self.view.guild.get_role(int(selected_role['target_role_id'])) if selected_role['target_role_id'] else None
        
        # Создаем embed для подтверждения
        embed = discord.Embed(
            title="⚠️ Подтверждение удаления",
            description=f"Вы уверены, что хотите удалить отслеживание этой роли?",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="📡 Сервер-источник",
            value=f"**Имя:** {selected_role['source_server_name']}\n**ID:** `{selected_role['source_server_id']}`",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Отслеживаемая роль",
            value=f"**Имя:** {selected_role['source_role_name']}\n**ID:** `{selected_role['source_role_id']}`",
            inline=False
        )
        
        embed.add_field(
            name="🗑️ Роль на этом сервере",
            value=f"{target_role.mention if target_role else '❌ Роль не найдена'}\n**Имя:** {selected_role['target_role_name']}\n**ID:** `{selected_role['target_role_id']}`",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Последствия удаления:",
            value="• Роль будет удалена из отслеживания\n"
                  "• Доступ к каналам будет убран\n"
                  "• Роль останется на сервере (можно удалить вручную)\n"
                  "• Все пользователи потеряют доступ\n"
                  "• Пользователи без других ролей будут забанены",
            inline=False
        )
        
        view = ConfirmDeleteView(selected_id, target_role, selected_role)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ConfirmDeleteView(discord.ui.View):
    """Кнопки подтверждения удаления"""
    def __init__(self, role_id: int, target_role: discord.Role, role_data: dict):
        super().__init__(timeout=60)
        self.role_id = role_id
        self.target_role = target_role
        self.role_data = role_data
    
    @discord.ui.button(label="✅ Да, удалить", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Удаляем из базы данных
            db.deactivate_tracked_role(self.role_id)
            
            # Убираем доступ к каналам если роль существует
            if self.target_role:
                # Получаем настройки сервера
                server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
                settings = db.get_server_settings(server_data['id'])
                
                if settings:
                    # Убираем доступ ко всем каналам
                    channel_ids = []
                    
                    # Добавляем текстовые каналы
                    for key in ['news_channel_id', 'flood_channel_id', 'tags_channel_id', 'media_channel_id']:
                        if settings.get(key):
                            channel_ids.append(settings[key])
                    
                    # Добавляем голосовые каналы
                    if settings.get('voice_channel_ids'):
                        try:
                            voice_ids = json.loads(settings['voice_channel_ids'])
                            channel_ids.extend(voice_ids)
                        except:
                            pass
                    
                    for channel_id in channel_ids:
                        if channel_id:
                            try:
                                channel = interaction.guild.get_channel(int(channel_id))
                                if channel:
                                    # Убираем все права
                                    await channel.set_permissions(self.target_role, overwrite=None)
                            except Exception as e:
                                logger.error(f"❌ Ошибка удаления прав: {e}")
            
            # Удаляем роль у всех пользователей
            if self.target_role:
                members_with_role = [member for member in interaction.guild.members if self.target_role in member.roles]
                for member in members_with_role:
                    try:
                        await member.remove_roles(self.target_role, reason="Удаление отслеживаемой роли")
                    except Exception as e:
                        logger.error(f"❌ Ошибка удаления роли у {member}: {e}")
            
            # Логируем удаление
            await Logger.log_to_channel(
                interaction.guild,
                f"**🗑️ Отслеживаемая роль удалена**\n"
                f"• Администратор: {interaction.user.mention}\n"
                f"• Сервер-источник: {self.role_data['source_server_name']}\n"
                f"• Отслеживаемая роль: {self.role_data['source_role_name']}\n"
                f"• Роль на сервере: {self.target_role.mention if self.target_role else 'Удалена'}\n"
                f"• Пользователей с ролью: {len(members_with_role) if self.target_role else 0}\n"
                f"• Время: {datetime.now().strftime('%H:%M:%S')}",
                discord.Color.red()
            )
            
            # Отправляем подтверждение
            embed = discord.Embed(
                title="✅ Роль удалена из отслеживания",
                description=f"Отслеживание роли успешно удалено.",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="📡 Удаленная роль",
                value=f"**Сервер:** {self.role_data['source_server_name']}\n**Роль:** {self.role_data['source_role_name']}",
                inline=False
            )
            
            if self.target_role:
                embed.add_field(
                    name="⚠️ Что сделано:",
                    value=f"• Роль {self.target_role.mention} удалена из отслеживания\n"
                          f"• Доступ к каналам убран\n"
                          f"• Роль снята с {len(members_with_role)} пользователей\n"
                          f"• Роль осталась на сервере (удалите вручную если нужно)",
                    inline=False
                )
            
            # Обновляем интерфейс
            for item in self.children:
                item.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
            
        except Exception as e:
            logger.error(f"❌ Ошибка удаления роли: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка при удалении роли: {str(e)}",
                ephemeral=True
            )
    
    @discord.ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❌ Удаление отменено",
            description="Роль не была удалена.",
            color=discord.Color.red()
        )
        
        # Отключаем все кнопки
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)

# ========== КЛАСС ДЛЯ НАСТРОЙКИ ДОСТУПА К КАНАЛАМ ==========
class ChannelPermissions:
    @staticmethod
    async def setup_channel_permissions(guild: discord.Guild, channel: discord.TextChannel, 
                                       admin_role1: discord.Role, admin_role2: discord.Role):
        """Настройка прав доступа для канала (изначально все закрыто)"""
        # Сбрасываем все права
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        # Применяем права
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
                    send_messages=False,  # Только читать
                    read_message_history=True
                )
                logger.info(f"✅ Добавлен доступ к news для роли {role.name} (только чтение)")
                configured_count += 1
        
        # 2. Flood - читать и писать
        if settings.get('flood_channel_id'):
            flood_channel = guild.get_channel(int(settings['flood_channel_id']))
            if flood_channel:
                await flood_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,  # Читать и писать
                    read_message_history=True
                )
                logger.info(f"✅ Добавлен доступ к flood для роли {role.name} (чтение/запись)")
                configured_count += 1
        
        # 3. Tags - только читать
        if settings.get('tags_channel_id'):
            tags_channel = guild.get_channel(int(settings['tags_channel_id']))
            if tags_channel:
                await tags_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=False,  # Только читать
                    read_message_history=True
                )
                logger.info(f"✅ Добавлен доступ к tags для роли {role.name} (только чтение)")
                configured_count += 1
        
        # 4. Media - читать и писать
        if settings.get('media_channel_id'):
            media_channel = guild.get_channel(int(settings['media_channel_id']))
            if media_channel:
                await media_channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,  # Читать и писать
                    read_message_history=True,
                    attach_files=True
                )
                logger.info(f"✅ Добавлен доступ к media для роли {role.name} (чтение/запись)")
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
                            connect=True,  # Подключаться
                            speak=True,    # Говорить
                            stream=True
                        )
                        configured_count += 1
                logger.info(f"✅ Добавлен доступ к голосовым каналам для роли {role.name}")
            except Exception as e:
                logger.error(f"❌ Ошибка настройки голосовых каналов: {e}")
        
        return configured_count

# ========== КЛАСС ДЛЯ ЛОГИРОВАНИЯ ==========
class Logger:
    @staticmethod
    async def log_to_channel(guild: discord.Guild, message: str, color: discord.Color = discord.Color.blue()):
        """Отправить лог в канал logs"""
        try:
            # Получаем настройки сервера
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            settings = db.get_server_settings(server_data['id'])
            
            logs_channel_id = settings.get('logs_channel_id')
            if not logs_channel_id:
                logger.warning(f"⚠️ Канал logs не найден для сервера {guild.name}")
                return
            
            logs_channel = guild.get_channel(int(logs_channel_id))
            if not logs_channel:
                logger.warning(f"⚠️ Не удалось получить канал logs для сервера {guild.name}")
                return
            
            # Создаем embed
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
            f"• Команда: `/{command}`\n"
            f"• Пользователь: {interaction.user.mention}\n"
            f"• ID: `{interaction.user.id}`\n"
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
            f"• ID: `{user.id}`\n"
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
            f"• ID: `{user.id}`\n"
            f"• Причина: {reason}\n"
            f"• Длительность: 10 минут\n"
            f"• Разбан: {unban_time.strftime('%H:%M:%S')}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.red()
        )
    
    @staticmethod
    async def log_unban(guild: discord.Guild, user_id: str, username: str, reason: str = ""):
        """Логирование разбана"""
        await Logger.log_to_channel(
            guild,
            f"**🔓 Пользователь разбанен**\n"
            f"• Пользователь: `{username}`\n"
            f"• ID: `{user_id}`\n"
            f"• Причина: {reason}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
    
    @staticmethod
    async def log_error(guild: discord.Guild, error: str, context: str = ""):
        """Логирование ошибки"""
        await Logger.log_to_channel(
            guild,
            f"**❌ Ошибка**\n"
            f"• Контекст: {context}\n"
            f"• Ошибка: {error}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.red()
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
                    await Logger.log_role_action(
                        guild, user, "✅ Роль добавлена", target_role, "Синхронизация ролей"
                    )
                    actions.append(f"➕ Добавлена {target_role.name}")
                
                elif not has_source_role and target_role in user.roles:
                    await user.remove_roles(target_role, reason="Синхронизация ролей")
                    await Logger.log_role_action(
                        guild, user, "🗑️ Роль удалена", target_role, "Потеря роли на исходном сервере"
                    )
                    actions.append(f"➖ Удалена {target_role.name}")
            
            # Если нет ни одной роли - бан на 10 минут
            if not user_has_any_role and user_id not in [int(b['user_id']) for b in db.get_banned_users(server_data['id'])]:
                await self.ban_user(guild, user_id, user.display_name, "Отсутствие требуемых ролей")
                actions.append("🔨 Бан на 10 минут")
            
            # Логируем проверку если были изменения
            if actions:
                await Logger.log_to_channel(
                    guild,
                    f"**🔍 Автопроверка пользователя**\n"
                    f"• Пользователь: {user.mention}\n"
                    f"• ID: `{user.id}`\n"
                    f"• Статус: {'✅ Есть роли' if user_has_any_role else '❌ Нет ролей'}\n"
                    f"• Действия: {', '.join(actions)}\n"
                    f"• Время: {datetime.now().strftime('%H:%M:%S')}",
                    discord.Color.purple()
                )
            
            return len(actions) > 0
            
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации: {e}")
            await Logger.log_error(guild, str(e), f"Синхронизация пользователя {user_id}")
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
                await Logger.log_ban(guild, user, reason)
            else:
                user_obj = await self.bot.fetch_user(user_id)
                await guild.ban(user_obj, reason=f"{reason} | Автобан на 10 минут", delete_message_days=0)
                await Logger.log_ban(guild, user_obj, reason)
            
            logger.info(f"🔨 Пользователь {username} забанен на 10 минут")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка бана: {e}")
            await Logger.log_error(guild, str(e), f"Бан пользователя {username}")
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
                        logger.info(f"🔓 Авторазбан {banned['username']}")
                except Exception as e:
                    logger.error(f"❌ Ошибка авторазбана: {e}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка в авторазбане: {e}")
    
    @tasks.loop(seconds=3)
    async def monitor_roles_task(self):
        """Фоновая задача для мониторинга ролей каждые 3 секунды"""
        try:
            # Разбан пользователей
            await self.auto_unban_users()
            
            # Мониторинг ролей на всех серверах
            for guild in self.bot.guilds:
                try:
                    server_data = db.get_or_create_server(str(guild.id), guild.name)
                    tracked_roles = db.get_tracked_roles(server_data['id'])
                    
                    if not tracked_roles:
                        continue
                    
                    # Получаем только недавно не проверенных пользователей
                    members = [m for m in guild.members if not m.bot]
                    
                    # Проверяем 3 пользователей за раз (чтобы не перегружать)
                    for member in members[:3]:
                        if not member.bot:
                            await self.sync_user_roles(guild, member.id)
                            await asyncio.sleep(0.1)  # Маленькая задержка
                            
                except Exception as e:
                    logger.error(f"❌ Ошибка мониторинга {guild.name}: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в задаче мониторинга: {e}")

# Инициализация монитора
role_monitor = RoleMonitor(bot)

# ========== КОМАНДЫ БОТА ==========
@bot.event
async def on_ready():
    """Событие при запуске бота"""
    print(f'✅ Бот {bot.user} запущен!')
    print(f'🆔 ID бота: {bot.user.id}')
    print(f'📊 Количество серверов: {len(bot.guilds)}')
    
    # Синхронизация команд
    try:
        synced = await bot.tree.sync()
        print(f'🔄 Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'❌ Ошибка синхронизации: {e}')
    
    # Запуск мониторинга каждые 3 секунды
    role_monitor.monitor_roles_task.start()
    print('👁️ Мониторинг ролей запущен (каждые 3 секунды)')

# ========== КОМАНДА /REMOVE_ROLE ==========
@bot.tree.command(name="remove_role", description="Удалить отслеживаемую роль с другого сервера")
@app_commands.checks.has_permissions(administrator=True)
async def remove_tracked_role(interaction: discord.Interaction):
    """Удалить отслеживаемую роль"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Логируем команду
        await Logger.log_command(interaction, "remove_role")
        
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            await interaction.followup.send(
                "ℹ️ Нет отслеживаемых ролей для удаления.",
                ephemeral=True
            )
            return
        
        # Создаем embed с информацией
        embed = discord.Embed(
            title="🗑️ Удаление отслеживаемой роли",
            description="Выберите роль из списка ниже для удаления:",
            color=discord.Color.orange()
        )
        
        embed.add_field(
            name="📋 Доступные роли",
            value=f"Найдено {len(tracked_roles)} отслеживаемых ролей",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Внимание",
            value="При удалении роли:\n• Прекратится отслеживание\n• Уберется доступ к каналам\n• Роль останется на сервере\n• Пользователи без других ролей будут забанены",
            inline=False
        )
        
        # Создаем представление с выпадающим списком
        view = DeleteRoleView(guild, tracked_roles)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        logger.error(f"❌ Ошибка команды remove_role: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /remove_role")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /SETT ==========
@bot.tree.command(name="sett", description="Настройка сервера: создание каналов и админских ролей")
@app_commands.checks.has_permissions(administrator=True)
async def setup_server(interaction: discord.Interaction):
    """Создает структуру сервера без обычных ролей - ВСЕ КАНАЛЫ ЗАКРЫТЫ"""
    
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    try:
        # Логируем команду
        await Logger.log_command(interaction, "sett")
        
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
        
        # 2. СОЗДАНИЕ ТЕКСТОВЫХ КАНАЛОВ (ВСЕ ИЗНАЧАЛЬНО ЗАКРЫТЫ)
        
        # Базовые права: все закрыто, только админы видят
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            admin_role1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            admin_role2: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        
        # 1.1 News - создаем закрытым
        news_channel = await guild.create_text_channel(
            name="news",
            topic="📢 Новости сервера (только для чтения)",
            overwrites=base_overwrites,  # Закрытый
            reason="Канал News из команды /sett"
        )
        
        # 1.2 Flood - создаем закрытым
        flood_channel = await guild.create_text_channel(
            name="flood",
            topic="💬 Общий чат для всех",
            overwrites=base_overwrites,  # Закрытый
            reason="Канал Flood из команды /sett"
        )
        
        # 1.3 Tags - создаем закрытым
        tags_channel = await guild.create_text_channel(
            name="tags",
            topic="🏷️ Теги (только для админов)",
            overwrites=base_overwrites,  # Закрытый
            reason="Канал Tags из команды /sett"
        )
        
        # 1.4 Media - создаем закрытым
        media_channel = await guild.create_text_channel(
            name="media",
            topic="🖼️ Медиа-контент",
            overwrites=base_overwrites,  # Закрытый
            reason="Канал Media из команды /sett"
        )
        
        logger.info(f"✅ Созданы текстовые каналы (все закрыты)")
        
        # 3. ЗАКРЫТЫЕ КАНАЛЫ (ТОЛЬКО ДЛЯ АДМИНОВ)
        
        # 1.5 Logs - только для админов
        logs_channel = await guild.create_text_channel(
            name="logs",
            topic="📊 Логи сервера (только для админов)",
            overwrites=base_overwrites,
            reason="Канал Logs из команды /sett"
        )
        
        # 1.6 High-flood - только для админов
        high_flood_channel = await guild.create_text_channel(
            name="high-flood",
            topic="🚨 Высокоуровневый чат (только для админов)",
            overwrites=base_overwrites,
            reason="Канал High-flood из команды /sett"
        )
        
        logger.info(f"✅ Созданы закрытые каналы")
        
        # 4. ГОЛОСОВЫЕ КАНАЛЫ (4 штуки, все закрыты)
        voice_channels = []
        for i in range(1, 5):
            voice_channel = await guild.create_voice_channel(
                name=f"Голосовой-{i}",
                overwrites=base_overwrites,  # Закрытый
                reason=f"Голосовой канал {i} из команды /sett"
            )
            voice_channels.append(voice_channel)
        
        logger.info(f"✅ Созданы голосовые каналы (все закрыты)")
        
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
            'voice_channel_ids': [str(vc.id) for vc in voice_channels]
        }
        db.save_server_settings(server_data['id'], settings)
        
        # 6. ОТЧЕТ
        embed = discord.Embed(
            title="🎉 Настройка сервера завершена!",
            description="Все каналы созданы закрытыми. Добавьте роли через `/serv` чтобы открыть доступ.",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="👑 Админские роли",
            value=f"{admin_role1.mention}\n{admin_role2.mention}",
            inline=False
        )
        
        embed.add_field(
            name="💬 Текстовые каналы (закрыты)",
            value=f"{news_channel.mention} - news (только чтение при добавлении роли)\n"
                  f"{flood_channel.mention} - flood (чтение/запись при добавлении роли)\n"
                  f"{tags_channel.mention} - tags (только чтение при добавлении роли)\n"
                  f"{media_channel.mention} - media (чтение/запись при добавлении роли)",
            inline=False
        )
        
        embed.add_field(
            name="🔒 Закрытые каналы (только админы)",
            value=f"{logs_channel.mention} - logs\n"
                  f"{high_flood_channel.mention} - high-flood",
            inline=False
        )
        
        embed.add_field(
            name="🎤 Голосовые каналы (закрыты)",
            value="\n".join([vc.mention for vc in voice_channels]),
            inline=False
        )
        
        embed.add_field(
            name="📋 Что делать дальше:",
            value="1. Используйте `/serv ID_сервера ID_роли` чтобы добавить отслеживаемую роль\n"
                  "2. Используйте `/remove_role` чтобы удалить отслеживаемую роль\n"
                  "3. Бот создаст роль с именем сервера\n"
                  "4. Настроит доступ к каналам согласно правам:\n"
                  "   • News - только чтение\n"
                  "   • Flood - чтение/запись\n"
                  "   • Tags - только чтение\n"
                  "   • Media - чтение/запись + файлы\n"
                  "   • Голосовые - подключение + голос",
            inline=False
        )
        
        embed.set_footer(text=f"Настроено пользователем {interaction.user.display_name}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # 7. ЛОГИРОВАНИЕ В КАНАЛ LOGS
        await Logger.log_to_channel(
            guild,
            f"**🎉 Сервер настроен**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Создано ролей: 2 (админские)\n"
            f"• Создано текстовых каналов: 6 (все закрыты)\n"
            f"• Создано голосовых каналов: 4 (все закрыты)\n"
            f"• Примечание: Все каналы закрыты. Добавляйте роли через /serv\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
        
        logger.info(f"✅ Сервер {guild.name} настроен (все каналы закрыты)")
        
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
        # Логируем команду
        await Logger.log_command(interaction, "serv")
        
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
        tracked_roles = db.get_tracked_roles(server_data['id'])
        for role in tracked_roles:
            if role['source_server_id'] == source_server_id and role['source_role_id'] == source_role_id:
                await interaction.followup.send(
                    f"❌ Роль уже отслеживается!",
                    ephemeral=True
                )
                return
        
        # 1. СОЗДАЕМ РОЛЬ НА ТЕКУЩЕМ СЕРВЕРЕ
        # Имя роли = имя сервера-источника (обрезаем до 32 символов)
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
        
        logger.info(f"✅ Создана роль {target_role.name} для сервера {source_guild.name}")
        
        # 2. НАСТРАИВАЕМ ДОСТУП К КАНАЛАМ С СООТВЕТСТВУЮЩИМИ ПРАВАМИ
        # Получаем настройки сервера
        settings = db.get_server_settings(server_data['id'])
        
        if not settings:
            await interaction.followup.send(
                "❌ Сервер не настроен! Сначала используйте `/sett`",
                ephemeral=True
            )
            return
        
        # Настраиваем доступ к каналам
        configured_count = await ChannelPermissions.add_role_to_channels(guild, target_role, settings)
        
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
        
        # Получаем каналы для отображения
        news_channel = guild.get_channel(int(settings['news_channel_id'])) if settings.get('news_channel_id') else None
        flood_channel = guild.get_channel(int(settings['flood_channel_id'])) if settings.get('flood_channel_id') else None
        tags_channel = guild.get_channel(int(settings['tags_channel_id'])) if settings.get('tags_channel_id') else None
        media_channel = guild.get_channel(int(settings['media_channel_id'])) if settings.get('media_channel_id') else None
        
        embed.add_field(
            name="🔓 Настроен доступ к каналам:",
            value=f"• {news_channel.mention if news_channel else 'News'} - **только чтение**\n"
                  f"• {flood_channel.mention if flood_channel else 'Flood'} - **чтение и запись**\n"
                  f"• {tags_channel.mention if tags_channel else 'Tags'} - **только чтение**\n"
                  f"• {media_channel.mention if media_channel else 'Media'} - **чтение, запись, файлы**\n"
                  f"• Голосовые каналы - **подключение и голос**",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Мониторинг",
            value=f"• Проверка: каждые 3 секунды\n• Автобан при потере роли: 10 минут\n• Авторазбан: через 10 минут\n• Настроено каналов: {configured_count}",
            inline=False
        )
        
        embed.set_footer(text="Теперь бот будет отслеживать эту роль и выдавать/убирать доступ автоматически")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # 5. ЛОГИРОВАНИЕ В КАНАЛ LOGS
        await Logger.log_to_channel(
            guild,
            f"**📡 Добавлена отслеживаемая роль**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Сервер-источник: {source_guild.name}\n"
            f"• Отслеживаемая роль: {source_role.name}\n"
            f"• Созданная роль: {target_role.mention}\n"
            f"• Настроен доступ к каналам:\n"
            f"  - News: только чтение\n"
            f"  - Flood: чтение/запись\n"
            f"  - Tags: только чтение\n"
            f"  - Media: чтение/запись + файлы\n"
            f"  - Голосовые: подключение + голос\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
        
        # 6. СРАЗУ ПРОВЕРЯЕМ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
        await interaction.followup.send(
            "🔄 Начинаю проверку всех пользователей...",
            ephemeral=True
        )
        
        members = [m for m in guild.members if not m.bot]
        checked = 0
        updated = 0
        
        for member in members:
            checked += 1
            if await role_monitor.sync_user_roles(guild, member.id):
                updated += 1
            await asyncio.sleep(0.1)
        
        await Logger.log_to_channel(
            guild,
            f"**🔄 Первоначальная проверка пользователей**\n"
            f"• Проверено пользователей: {checked}\n"
            f"• Обновлено ролей: {updated}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.blue()
        )
        
        await interaction.followup.send(
            f"✅ Проверено {checked} пользователей, обновлено {updated}",
            ephemeral=True
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка команды /serv: {e}")
        await Logger.log_error(guild, str(e), "Команда /serv")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /CHECK_USER ==========
@bot.tree.command(name="check_user", description="Проверить роли конкретного пользователя")
@app_commands.describe(user="Пользователь для проверки")
@app_commands.checks.has_permissions(administrator=True)
async def check_user(interaction: discord.Interaction, user: discord.Member):
    """Проверить роли пользователя"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Логируем команду
        await Logger.log_command(interaction, "check_user")
        
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
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Логируем проверку
        await Logger.log_to_channel(
            interaction.guild,
            f"**🔍 Ручная проверка пользователя**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Пользователь: {user.mention}\n"
            f"• Статус: {'✅ Есть роли' if has_role else '❌ Нет ролей'}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.purple()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /check_user")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /SYNC_ALL ==========
@bot.tree.command(name="sync_all", description="Синхронизировать всех пользователей")
@app_commands.checks.has_permissions(administrator=True)
async def sync_all(interaction: discord.Interaction):
    """Синхронизировать всех пользователей на сервере"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Логируем команду
        await Logger.log_command(interaction, "sync_all")
        
        guild = interaction.guild
        members = [m for m in guild.members if not m.bot]
        
        await interaction.followup.send(
            f"🔄 Начинаю синхронизацию {len(members)} пользователей...",
            ephemeral=True
        )
        
        processed = 0
        updated = 0
        banned = 0
        
        for member in members:
            processed += 1
            if await role_monitor.sync_user_roles(guild, member.id):
                updated += 1
            
            # Проверяем, был ли пользователь забанен в этой сессии
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            banned_users = db.get_banned_users(server_data['id'])
            if member.id in [int(b['user_id']) for b in banned_users]:
                banned += 1
            
            # Обновляем статус каждые 10 пользователей
            if processed % 10 == 0:
                await interaction.edit_original_response(
                    content=f"🔄 Обработано {processed}/{len(members)} пользователей, обновлено {updated}, забанено {banned}"
                )
            
            await asyncio.sleep(0.1)
        
        embed = discord.Embed(
            title="✅ Синхронизация завершена",
            description=f"**Обработано:** {processed} пользователей\n**Обновлено:** {updated}\n**Забанено:** {banned}",
            color=discord.Color.green()
        )
        
        await interaction.edit_original_response(embed=embed)
        
        # Логируем завершение синхронизации
        await Logger.log_to_channel(
            guild,
            f"**🔄 Массовая синхронизация завершена**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Обработано: {processed} пользователей\n"
            f"• Обновлено: {updated}\n"
            f"• Забанено: {banned}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.green()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка синхронизации: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /sync_all")
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
        # Логируем команду
        await Logger.log_command(interaction, "unban")
        
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
        
        # Логируем разбан
        await Logger.log_unban(
            interaction.guild, 
            user_id, 
            user.name, 
            f"Разбан администратором {interaction.user}"
        )
        
    except discord.NotFound:
        await interaction.followup.send(
            "❌ Пользователь не найден или не забанен",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"❌ Ошибка разбана: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /unban")
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
        # Логируем команду
        await Logger.log_command(interaction, "list_roles")
        
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
        
        # Логируем просмотр ролей
        await Logger.log_to_channel(
            interaction.guild,
            f"**📋 Просмотр отслеживаемых ролей**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Количество ролей: {len(tracked_roles)}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.purple()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка списка ролей: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /list_roles")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /STATS ==========
@bot.tree.command(name="stats", description="Статистика сервера")
@app_commands.checks.has_permissions(administrator=True)
async def server_stats(interaction: discord.Interaction):
    """Показать статистику сервера"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Логируем команду
        await Logger.log_command(interaction, "stats")
        
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        tracked_roles = db.get_tracked_roles(server_data['id'])
        banned_users = db.get_banned_users(server_data['id'])
        
        embed = discord.Embed(
            title=f"📊 Статистика {guild.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Основная информация
        total_members = len([m for m in guild.members if not m.bot])
        bot_count = len([m for m in guild.members if m.bot])
        
        embed.add_field(
            name="👥 Участники",
            value=f"Всего: {guild.member_count}\nПользователи: {total_members}\nБоты: {bot_count}",
            inline=True
        )
        
        embed.add_field(
            name="🔨 Баны",
            value=f"Активных: {len(banned_users)}\nАвторазбан: через 10 мин",
            inline=True
        )
        
        # Отслеживаемые роли
        embed.add_field(
            name=f"📡 Отслеживаемые роли",
            value=f"Количество: {len(tracked_roles)}",
            inline=True
        )
        
        # Каналы
        text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
        voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
        
        embed.add_field(
            name="💬 Каналы",
            value=f"Текстовые: {text_channels}\nГолосовые: {voice_channels}",
            inline=True
        )
        
        # Мониторинг
        embed.add_field(
            name="👁️ Мониторинг",
            value="Статус: ✅ Активен\nПроверка: каждые 3 сек",
            inline=True
        )
        
        # Статус каналов
        settings = db.get_server_settings(server_data['id'])
        channel_status = "✅ Настроены" if settings else "❌ Не настроены"
        
        embed.add_field(
            name="🔧 Статус каналов",
            value=f"Каналы: {channel_status}\nДоступ: только через роли",
            inline=False
        )
        
        embed.set_footer(text=f"ID сервера: {guild.id}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Логируем просмотр статистики
        await Logger.log_to_channel(
            interaction.guild,
            f"**📊 Просмотр статистики**\n"
            f"• Администратор: {interaction.user.mention}\n"
            f"• Участники: {total_members}\n"
            f"• Отслеживаемые роли: {len(tracked_roles)}\n"
            f"• Активные баны: {len(banned_users)}\n"
            f"• Время: {datetime.now().strftime('%H:%M:%S')}",
            discord.Color.blue()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка статистики: {e}")
        await Logger.log_error(interaction.guild, str(e), "Команда /stats")
        await interaction.followup.send(
            f"❌ Ошибка: {str(e)}",
            ephemeral=True
        )

# ========== КОМАНДА /PING ==========
@bot.tree.command(name="ping", description="Проверка задержки бота")
async def ping_command(interaction: discord.Interaction):
    """Проверить задержку бота"""
    
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

# ========== ОБРАБОТКА ОШИБОК КОМАНД ==========
@setup_server.error
@add_server_role.error
@remove_tracked_role.error
@check_user.error
@sync_all.error
@unban_user.error
@list_tracked_roles.error
@server_stats.error
async def command_error(interaction: discord.Interaction, error):
    """Обработчик ошибок команд"""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ У вас недостаточно прав для выполнения этой команды!",
            ephemeral=True
        )
    else:
        logger.error(f"❌ Ошибка команды: {error}")
        await interaction.response.send_message(
            f"❌ Произошла ошибка: {str(error)}",
            ephemeral=True
        )
        
        # Логируем ошибку в канал logs
        if interaction.guild:
            await Logger.log_error(
                interaction.guild,
                str(error),
                f"Команда {interaction.command.name if interaction.command else 'unknown'}"
            )

# ========== ЗАПУСК БОТА ==========
if __name__ == "__main__":
    print("🚀 Запуск Discord бота...")
    print(f"📦 Версия discord.py: {discord.__version__}")
    print("⚙️ Настройки:")
    print(f"  • Все каналы закрыты при создании")
    print(f"  • Проверка ролей: каждые 3 секунды")
    print(f"  • Автобан: 10 минут")
    print(f"  • Логирование: в канал 'logs'")
    print(f"  • База данных: PostgreSQL (таблицы создаются автоматически)")
    print(f"  • Доступ к каналам при добавлении роли:")
    print(f"    - News: только чтение")
    print(f"    - Flood: чтение и запись")
    print(f"    - Tags: только чтение")
    print(f"    - Media: чтение, запись, файлы")
    print(f"    - Голосовые: подключение, голос")
    print(f"  • Новые команды:")
    print(f"    - /remove_role - удалить отслеживаемую роль")
    
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")