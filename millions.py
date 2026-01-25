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
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = None
        self.use_sqlite = False
        self.connect()
    
    def connect(self):
        try:
            database_url = os.getenv('DATABASE_URL')
            if database_url and database_url.startswith('postgresql://'):
                database_url = database_url.replace('postgresql://', 'postgres://')
            
            if database_url:
                import psycopg2
                from psycopg2.extras import RealDictCursor
                self.conn = psycopg2.connect(database_url, sslmode='require', cursor_factory=RealDictCursor)
                self.conn.autocommit = True
                logger.info("✅ Подключено к PostgreSQL")
            else:
                import sqlite3
                self.use_sqlite = True
                self.conn = sqlite3.connect('bot_database.db', check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                logger.info("✅ Создана SQLite база")
        except:
            self.conn = None
    
    def execute(self, query, params=None, fetchone=False, fetchall=False):
        if not self.conn:
            return None
        
        for attempt in range(2):
            try:
                cursor = self.conn.cursor()
                if self.use_sqlite:
                    query = query.replace('%s', '?')
                cursor.execute(query, params or ())
                
                if fetchone:
                    result = cursor.fetchone()
                elif fetchall:
                    result = cursor.fetchall()
                else:
                    result = cursor.rowcount
                
                if self.use_sqlite:
                    self.conn.commit()
                cursor.close()
                return result
            except Exception as e:
                try:
                    cursor.close()
                except:
                    pass
                if attempt == 1:
                    logger.error(f"❌ SQL ошибка: {e}")
                time.sleep(0.5)
        return None
    
    def create_tables(self):
        if not self.conn:
            return
        
        tables = [
            '''CREATE TABLE IF NOT EXISTS servers (id SERIAL PRIMARY KEY, discord_id VARCHAR(255) NOT NULL, name VARCHAR(255) NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS server_settings (id SERIAL PRIMARY KEY, server_id INTEGER NOT NULL, news_channel_id VARCHAR(255), flood_channel_id VARCHAR(255), tags_channel_id VARCHAR(255), media_channel_id VARCHAR(255), logs_channel_id VARCHAR(255), voice_channel_ids TEXT)''',
            '''CREATE TABLE IF NOT EXISTS tracked_roles (id SERIAL PRIMARY KEY, server_id INTEGER NOT NULL, source_server_id VARCHAR(255) NOT NULL, source_role_id VARCHAR(255) NOT NULL, target_role_id VARCHAR(255), is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE IF NOT EXISTS banned_users (id SERIAL PRIMARY KEY, server_id INTEGER NOT NULL, user_id VARCHAR(255) NOT NULL, username VARCHAR(255) NOT NULL, unban_time TIMESTAMP, is_unbanned BOOLEAN DEFAULT FALSE)'''
        ]
        
        for table in tables:
            self.execute(table)
    
    def get_or_create_server(self, discord_id: str, name: str):
        result = self.execute('SELECT * FROM servers WHERE discord_id = %s', (discord_id,), fetchone=True)
        if result:
            return dict(result)
        
        self.execute('INSERT INTO servers (discord_id, name) VALUES (%s, %s)', (discord_id, name))
        return self.execute('SELECT * FROM servers WHERE discord_id = %s', (discord_id,), fetchone=True)
    
    def save_settings(self, server_id: int, settings: dict):
        voice_ids = json.dumps(settings.get('voice_channel_ids', []))
        
        if self.use_sqlite:
            self.execute('''INSERT OR REPLACE INTO server_settings (server_id, news_channel_id, flood_channel_id, tags_channel_id, media_channel_id, logs_channel_id, voice_channel_ids) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (server_id, settings.get('news_channel_id'), settings.get('flood_channel_id'), settings.get('tags_channel_id'), settings.get('media_channel_id'), settings.get('logs_channel_id'), voice_ids))
        else:
            self.execute('''INSERT INTO server_settings (server_id, news_channel_id, flood_channel_id, tags_channel_id, media_channel_id, logs_channel_id, voice_channel_ids) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (server_id) DO UPDATE SET news_channel_id=EXCLUDED.news_channel_id, flood_channel_id=EXCLUDED.flood_channel_id, tags_channel_id=EXCLUDED.tags_channel_id, media_channel_id=EXCLUDED.media_channel_id, logs_channel_id=EXCLUDED.logs_channel_id, voice_channel_ids=EXCLUDED.voice_channel_ids''',
                        (server_id, settings.get('news_channel_id'), settings.get('flood_channel_id'), settings.get('tags_channel_id'), settings.get('media_channel_id'), settings.get('logs_channel_id'), voice_ids))
    
    def get_settings(self, server_id: int):
        result = self.execute('SELECT * FROM server_settings WHERE server_id = %s', (server_id,), fetchone=True)
        return dict(result) if result else {}
    
    def add_tracked_role(self, server_id: int, source_server_id: str, source_role_id: str):
        result = self.execute('SELECT id FROM tracked_roles WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s AND is_active = TRUE',
                            (server_id, source_server_id, source_role_id), fetchone=True)
        if result:
            return result['id']
        
        self.execute('INSERT INTO tracked_roles (server_id, source_server_id, source_role_id) VALUES (%s, %s, %s)',
                    (server_id, source_server_id, source_role_id))
        result = self.execute('SELECT id FROM tracked_roles WHERE server_id = %s AND source_server_id = %s AND source_role_id = %s',
                            (server_id, source_server_id, source_role_id), fetchone=True)
        return result['id'] if result else None
    
    def update_target_role(self, tracked_id: int, target_role_id: str):
        self.execute('UPDATE tracked_roles SET target_role_id = %s WHERE id = %s', (target_role_id, tracked_id))
    
    def get_tracked_roles(self, server_id: int):
        results = self.execute('SELECT * FROM tracked_roles WHERE server_id = %s AND is_active = TRUE ORDER BY created_at DESC', (server_id,), fetchall=True)
        return [dict(r) for r in results] if results else []
    
    def deactivate_tracked_role(self, role_id: int):
        self.execute('UPDATE tracked_roles SET is_active = FALSE WHERE id = %s', (role_id,))
    
    def get_tracked_role_by_id(self, role_id: int):
        result = self.execute('SELECT * FROM tracked_roles WHERE id = %s', (role_id,), fetchone=True)
        return dict(result) if result else None
    
    def get_tracked_role_by_source_id(self, server_id: int, source_role_id: str):
        result = self.execute('SELECT * FROM tracked_roles WHERE server_id = %s AND source_role_id = %s AND is_active = TRUE', 
                             (server_id, source_role_id), fetchone=True)
        return dict(result) if result else None
    
    def count_target_role_usage(self, target_role_id: str):
        result = self.execute('SELECT COUNT(*) as count FROM tracked_roles WHERE target_role_id = %s AND is_active = TRUE', 
                             (target_role_id,), fetchone=True)
        return result['count'] if result else 0
    
    def ban_user(self, server_id: int, user_id: str, username: str):
        unban = datetime.now() + timedelta(seconds=600)
        if self.use_sqlite:
            self.execute('INSERT OR REPLACE INTO banned_users (server_id, user_id, username, unban_time) VALUES (?, ?, ?, ?)',
                        (server_id, user_id, username, unban.isoformat()))
        else:
            self.execute('INSERT INTO banned_users (server_id, user_id, username, unban_time) VALUES (%s, %s, %s, %s) ON CONFLICT (server_id, user_id) DO UPDATE SET unban_time=EXCLUDED.unban_time, is_unbanned=FALSE',
                        (server_id, user_id, username, unban.isoformat()))
    
    def unban_user(self, server_id: int, user_id: str):
        self.execute('UPDATE banned_users SET is_unbanned = TRUE WHERE server_id = %s AND user_id = %s', (server_id, user_id))
    
    def get_banned_users(self, server_id: int):
        results = self.execute('SELECT * FROM banned_users WHERE server_id = %s AND is_unbanned = FALSE', (server_id,), fetchall=True)
        return [dict(r) for r in results] if results else []
    
    def get_users_to_unban(self):
        results = self.execute('SELECT * FROM banned_users WHERE is_unbanned = FALSE AND unban_time <= %s', (datetime.now().isoformat(),), fetchall=True)
        return [dict(r) for r in results] if results else []

db = Database()
db.create_tables()

# ========== МОДАЛЬНЫЕ ОКНА ==========
class AddRoleModal(discord.ui.Modal, title="Добавить отслеживаемую роль"):
    server_id = discord.ui.TextInput(label="ID сервера-источника", placeholder="Введите ID сервера...", required=True, max_length=20)
    role_id = discord.ui.TextInput(label="ID роли", placeholder="Введите ID роли...", required=True, max_length=20)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Обрабатываю...", ephemeral=True)
        await asyncio.sleep(0.5)
        await add_role(interaction, self.server_id.value, self.role_id.value)

class UnbanModal(discord.ui.Modal, title="Разблокировать пользователя"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="Введите ID пользователя...", required=True, max_length=20)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Обрабатываю...", ephemeral=True)
        await unban(interaction, self.user_id.value)

class RemoveRoleModal(discord.ui.Modal, title="Удалить отслеживаемую роль"):
    role_id = discord.ui.TextInput(
        label="ID отслеживаемой роли",
        placeholder="Введите ID роли (можно посмотреть через список ролей)...",
        required=True,
        max_length=20
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await remove_role_by_id(interaction, self.role_id.value)

# ========== ВЫПАДАЮЩЕЕ МЕНЮ ДЛЯ УДАЛЕНИЯ РОЛЕЙ ==========
class RoleSelectView(discord.ui.View):
    def __init__(self, tracked_roles, timeout=180):
        super().__init__(timeout=timeout)
        self.tracked_roles = tracked_roles
        
        # Создаем выпадающее меню
        select_menu = discord.ui.Select(
            placeholder="Выберите роль для удаления...",
            min_values=1,
            max_values=1,
            custom_id="role_select"
        )
        
        for role in tracked_roles:
            # Получаем информацию о роли
            source_guild = bot.get_guild(int(role['source_server_id']))
            source_role = None
            if source_guild:
                source_role = source_guild.get_role(int(role['source_role_id']))
            
            target_role = None
            if role['target_role_id']:
                # Найдем гильдию для целевой роли
                for guild in bot.guilds:
                    target_role = guild.get_role(int(role['target_role_id']))
                    if target_role:
                        break
            
            # Формируем текст для опции
            source_name = source_role.name if source_role else f"ID: {role['source_role_id']}"
            target_name = target_role.name if target_role else "Не назначена"
            label = f"{source_name} → {target_name}"[:100]
            
            select_menu.add_option(
                label=label,
                value=str(role['id']),
                description=f"Сервер: {source_guild.name[:50] if source_guild else 'Неизвестно'}"
            )
        
        select_menu.callback = self.select_callback
        self.add_item(select_menu)
    
    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.children[0].values[0])
        await interaction.response.defer(ephemeral=True)
        await confirm_remove_role(interaction, role_id)

class ConfirmRemoveView(discord.ui.View):
    def __init__(self, role_id, role_info):
        super().__init__(timeout=180)
        self.role_id = role_id
        self.role_info = role_info
    
    @discord.ui.button(label="✅ Да, удалить", style=discord.ButtonStyle.danger, row=0)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await execute_remove_role(interaction, self.role_id, self.role_info)
    
    @discord.ui.button(label="❌ Нет, отмена", style=discord.ButtonStyle.secondary, row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Удаление отменено", view=None, embed=None)

# ========== ПАНЕЛЬ УПРАВЛЕНИЯ ==========
class ControlPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="⚙️ Настройка сервера", style=discord.ButtonStyle.primary, custom_id="setup_btn", row=0)
    async def setup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await setup_server(interaction)
    
    @discord.ui.button(label="➕ Добавить роль", style=discord.ButtonStyle.success, custom_id="add_role_btn", row=0)
    async def add_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddRoleModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🗑️ Удалить роль", style=discord.ButtonStyle.danger, custom_id="remove_role_btn", row=0)
    async def remove_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await show_remove_role_menu(interaction)
    
    @discord.ui.button(label="📋 Список ролей", style=discord.ButtonStyle.secondary, custom_id="list_roles_btn", row=0)
    async def list_roles_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await list_roles(interaction)
    
    @discord.ui.button(label="🔄 Синхронизация", style=discord.ButtonStyle.primary, custom_id="sync_btn", row=1)
    async def sync_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await sync_all(interaction)
    
    @discord.ui.button(label="📊 Статистика", style=discord.ButtonStyle.secondary, custom_id="stats_btn", row=1)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await stats(interaction)
    
    @discord.ui.button(label="🔓 Разбан", style=discord.ButtonStyle.success, custom_id="unban_btn", row=1)
    async def unban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = UnbanModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🏓 Пинг", style=discord.ButtonStyle.secondary, custom_id="ping_btn", row=1)
    async def ping_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        latency = round(bot.latency * 1000)
        embed = discord.Embed(title="🏓 Понг!", description=f"Задержка: **{latency}ms**", color=discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 300 else discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== КЛАСС ДЛЯ РОЛЕЙ ==========
class RoleMonitor:
    def __init__(self, bot):
        self.bot = bot
    
    async def sync_user_roles(self, guild: discord.Guild, user_id: int):
        try:
            user = guild.get_member(user_id)
            if not user or not db.conn:
                return False
            
            server_data = db.get_or_create_server(str(guild.id), guild.name)
            if not server_data:
                return False
            
            tracked_roles = db.get_tracked_roles(server_data['id'])
            
            servers_roles = {}
            for tracked in tracked_roles:
                server_id = tracked['source_server_id']
                if server_id not in servers_roles:
                    servers_roles[server_id] = []
                servers_roles[server_id].append(tracked)
            
            for server_id, roles_list in servers_roles.items():
                if not roles_list or not roles_list[0]['target_role_id']:
                    continue
                
                target_role = guild.get_role(int(roles_list[0]['target_role_id']))
                if not target_role:
                    continue
                
                has_role = False
                for tracked in roles_list:
                    source_guild = self.bot.get_guild(int(tracked['source_server_id']))
                    if not source_guild:
                        continue
                    
                    source_member = source_guild.get_member(user_id)
                    if source_member:
                        source_role = source_guild.get_role(int(tracked['source_role_id']))
                        if source_role and source_role in source_member.roles:
                            has_role = True
                            break
                
                if has_role and target_role not in user.roles:
                    await user.add_roles(target_role, reason="Синхронизация")
                elif not has_role and target_role in user.roles:
                    await user.remove_roles(target_role, reason="Синхронизация")
            
            return True
        except:
            return False
    
    async def auto_unban_users(self):
        try:
            users_to_unban = db.get_users_to_unban()
            for banned in users_to_unban:
                try:
                    server = self.bot.get_guild(int(banned['server_id']))
                    if server:
                        user = await self.bot.fetch_user(int(banned['user_id']))
                        await server.unban(user, reason="Авторазбан")
                        db.unban_user(banned['server_id'], banned['user_id'])
                except:
                    pass
        except:
            pass
    
    @tasks.loop(seconds=3)
    async def monitor_roles_task(self):
        try:
            await self.auto_unban_users()
            for guild in self.bot.guilds:
                try:
                    members = [m for m in guild.members if not m.bot]
                    for member in members[:3]:
                        await self.sync_user_roles(guild, member.id)
                        await asyncio.sleep(0.1)
                except:
                    pass
        except:
            pass

role_monitor = RoleMonitor(bot)

# ========== ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ РОЛЯМИ ==========
async def show_remove_role_menu(interaction: discord.Interaction):
    """Показывает меню выбора роли для удаления"""
    try:
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        if not server_data:
            await interaction.followup.send("❌ Ошибка сервера", ephemeral=True)
            return
        
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            embed = discord.Embed(
                title="ℹ️ Нет отслеживаемых ролей",
                description="У вас нет активных отслеживаемых ролей.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Создаем меню выбора
        view = RoleSelectView(tracked_roles)
        
        embed = discord.Embed(
            title="🗑️ Удаление отслеживаемой роли",
            description="Выберите роль из списка ниже:",
            color=discord.Color.orange()
        )
        embed.set_footer(text="Выберите роль из выпадающего меню")
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Ошибка в show_remove_role_menu: {e}")
        await interaction.followup.send("❌ Произошла ошибка при загрузке меню", ephemeral=True)

async def confirm_remove_role(interaction: discord.Interaction, role_id: int):
    """Показывает подтверждение удаления роли"""
    try:
        role_data = db.get_tracked_role_by_id(role_id)
        if not role_data:
            await interaction.followup.send("❌ Роль не найдена", ephemeral=True)
            return
        
        # Получаем информацию о роли
        source_guild = bot.get_guild(int(role_data['source_server_id']))
        source_role = None
        if source_guild:
            source_role = source_guild.get_role(int(role_data['source_role_id']))
        
        target_role = None
        if role_data['target_role_id']:
            for guild in bot.guilds:
                target_role = guild.get_role(int(role_data['target_role_id']))
                if target_role:
                    break
        
        # Создаем embed с информацией
        embed = discord.Embed(
            title="⚠️ Подтверждение удаления",
            color=discord.Color.red()
        )
        
        if source_guild and source_role:
            embed.add_field(name="Сервер-источник", value=source_guild.name, inline=True)
            embed.add_field(name="Роль-источник", value=source_role.name, inline=True)
        else:
            embed.add_field(name="Сервер-источник", value=f"ID: {role_data['source_server_id']}", inline=True)
            embed.add_field(name="Роль-источник", value=f"ID: {role_data['source_role_id']}", inline=True)
        
        if target_role:
            embed.add_field(name="Целевая роль", value=target_role.mention, inline=False)
            
            # Проверяем, сколько пользователей имеют эту роль
            members_with_role = len([m for m in target_role.members if not m.bot])
            embed.add_field(name="Пользователей с ролью", value=str(members_with_role), inline=True)
            
            # Проверяем другие использования этой роли
            usage_count = db.count_target_role_usage(role_data['target_role_id'])
            embed.add_field(name="Используется в отслеживаниях", value=str(usage_count), inline=True)
        else:
            embed.add_field(name="Целевая роль", value="Не назначена", inline=False)
        
        embed.add_field(
            name="Внимание!", 
            value="Это действие нельзя отменить. Роль будет удалена из отслеживания.", 
            inline=False
        )
        
        view = ConfirmRemoveView(role_id, role_data)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Ошибка в confirm_remove_role: {e}")
        await interaction.followup.send("❌ Произошла ошибка", ephemeral=True)

async def execute_remove_role(interaction: discord.Interaction, role_id: int, role_data: dict):
    """Выполняет удаление роли"""
    try:
        # Деактивируем роль в базе данных
        db.deactivate_tracked_role(role_id)
        
        guild = interaction.guild
        embed = discord.Embed(title="✅ Роль удалена", color=discord.Color.green())
        
        # Получаем информацию о целевой роли
        if role_data['target_role_id']:
            target_role = guild.get_role(int(role_data['target_role_id']))
            
            if target_role:
                # Проверяем, используется ли эта роль в других отслеживаниях
                usage_count = db.count_target_role_usage(role_data['target_role_id'])
                
                if usage_count == 0:
                    # Роль больше не используется, можно удалить
                    members_count = len([m for m in target_role.members if not m.bot])
                    
                    if members_count > 0:
                        embed.add_field(
                            name="💡 Рекомендация", 
                            value=f"Целевая роль {target_role.mention} больше не используется в отслеживаниях, но назначена **{members_count}** пользователям. Вы можете удалить её вручную.",
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="💡 Информация", 
                            value=f"Целевая роль {target_role.mention} больше не используется. Вы можете безопасно удалить её.",
                            inline=False
                        )
                else:
                    embed.add_field(
                        name="ℹ️ Информация", 
                        value=f"Целевая роль {target_role.mention} используется в других отслеживаниях ({usage_count}).",
                        inline=False
                    )
        
        # Получаем информацию об исходной роли
        source_guild = bot.get_guild(int(role_data['source_server_id']))
        if source_guild:
            source_role = source_guild.get_role(int(role_data['source_role_id']))
            if source_role:
                embed.add_field(
                    name="Удаленная роль", 
                    value=f"**{source_role.name}** с сервера **{source_guild.name}**", 
                    inline=False
                )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Ошибка в execute_remove_role: {e}")
        await interaction.followup.send("❌ Произошла ошибка при удалении роли", ephemeral=True)

async def remove_role_by_id(interaction: discord.Interaction, role_id: str):
    """Удаляет роль по ID (для модального окна)"""
    try:
        if not role_id.isdigit():
            await interaction.followup.send("❌ ID роли должен быть числом", ephemeral=True)
            return
        
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        if not server_data:
            await interaction.followup.send("❌ Ошибка сервера", ephemeral=True)
            return
        
        # Ищем роль по source_role_id
        role_data = db.get_tracked_role_by_source_id(server_data['id'], role_id)
        
        if not role_data:
            await interaction.followup.send("❌ Роль с таким ID не найдена", ephemeral=True)
            return
        
        # Показываем подтверждение
        await confirm_remove_role(interaction, role_data['id'])
        
    except Exception as e:
        logger.error(f"Ошибка в remove_role_by_id: {e}")
        await interaction.followup.send("❌ Произошла ошибка", ephemeral=True)

# ========== ОСТАЛЬНЫЕ ФУНКЦИИ ==========
async def setup_server(interaction: discord.Interaction):
    try:
        await interaction.followup.send("🔄 Начинаю настройку...", ephemeral=True)
        guild = interaction.guild
        
        if not db.conn:
            await interaction.edit_original_response(content="❌ Ошибка базы данных")
            return
        
        db.create_tables()
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        # Создаем категории
        main_category = await guild.create_category(name="MAIN")
        high_category = await guild.create_category(name="HIGH")
        
        base_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False)
        }
        
        # Создаем каналы
        news = await main_category.create_text_channel(name="news", overwrites=base_overwrites)
        flood = await main_category.create_text_channel(name="flood", overwrites=base_overwrites)
        tags = await main_category.create_text_channel(name="tags", overwrites=base_overwrites)
        media = await main_category.create_text_channel(name="media", overwrites=base_overwrites)
        logs = await high_category.create_text_channel(name="logs", overwrites=base_overwrites)
        high_flood = await high_category.create_text_channel(name="high-flood", overwrites=base_overwrites)
        
        voice_channels = []
        for i in range(1, 5):
            voice = await main_category.create_voice_channel(name=f"voice {i}", overwrites=base_overwrites)
            voice_channels.append(voice)
        
        high_voice = await high_category.create_voice_channel(name="high-voice", overwrites=base_overwrites)
        
        # Сохраняем настройки
        settings = {
            'news_channel_id': str(news.id),
            'flood_channel_id': str(flood.id),
            'tags_channel_id': str(tags.id),
            'media_channel_id': str(media.id),
            'logs_channel_id': str(logs.id),
            'high_flood_channel_id': str(high_flood.id),
            'voice_channel_ids': [str(vc.id) for vc in voice_channels],
            'high_voice_channel_id': str(high_voice.id),
            'main_category_id': str(main_category.id),
            'high_category_id': str(high_category.id)
        }
        
        db.save_settings(server_data['id'], settings)
        
        embed = discord.Embed(title="✅ Сервер настроен", color=discord.Color.green())
        embed.add_field(name="📁 Категория MAIN", value=f"{news.mention} {flood.mention} {tags.mention} {media.mention}", inline=False)
        embed.add_field(name="📁 Категория HIGH", value=f"{logs.mention} {high_flood.mention} {high_voice.mention}", inline=False)
        
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ Ошибка: {str(e)[:100]}")

async def add_role(interaction: discord.Interaction, source_server_id: str, source_role_id: str):
    try:
        await interaction.edit_original_response(content="🔄 Проверяю...")
        
        guild = interaction.guild
        
        if not source_server_id.isdigit() or not source_role_id.isdigit():
            await interaction.edit_original_response(content="❌ ID должны быть числами")
            return
        
        source_guild = bot.get_guild(int(source_server_id))
        if not source_guild:
            await interaction.edit_original_response(content="❌ Сервер не найден")
            return
        
        source_role = source_guild.get_role(int(source_role_id))
        if not source_role:
            await interaction.edit_original_response(content="❌ Роль не найдена")
            return
        
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        if not server_data:
            await interaction.edit_original_response(content="❌ Ошибка сервера")
            return
        
        # Проверяем существующие роли
        tracked_roles = db.get_tracked_roles(server_data['id'])
        for role in tracked_roles:
            if role['source_server_id'] == source_server_id and role['source_role_id'] == source_role_id:
                await interaction.edit_original_response(content="❌ Роль уже отслеживается")
                return
        
        # Ищем существующую целевую роль
        existing_target_role = None
        for role in tracked_roles:
            if role['source_server_id'] == source_server_id and role['target_role_id']:
                target_role = guild.get_role(int(role['target_role_id']))
                if target_role:
                    existing_target_role = target_role
                    break
        
        if existing_target_role:
            target_role = existing_target_role
        else:
            role_name = source_guild.name[:32]
            target_role = await guild.create_role(name=role_name, color=discord.Color.random())
        
        # Сохраняем в БД
        tracked_id = db.add_tracked_role(server_data['id'], source_server_id, source_role_id)
        if tracked_id:
            db.update_target_role(tracked_id, str(target_role.id))
        
        embed = discord.Embed(title="✅ Роль добавлена", color=discord.Color.green())
        embed.add_field(name="Сервер", value=source_guild.name, inline=True)
        embed.add_field(name="Роль", value=source_role.name, inline=True)
        embed.add_field(name="Назначена", value=target_role.mention, inline=False)
        
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ Ошибка: {str(e)[:100]}")

async def list_roles(interaction: discord.Interaction):
    try:
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        if not server_data:
            await interaction.followup.send("❌ Ошибка", ephemeral=True)
            return
        
        tracked_roles = db.get_tracked_roles(server_data['id'])
        
        if not tracked_roles:
            await interaction.followup.send("ℹ️ Нет активных отслеживаемых ролей", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 Отслеживаемые роли", color=discord.Color.purple())
        
        for role in tracked_roles:
            # Получаем информацию о роли
            source_guild = bot.get_guild(int(role['source_server_id']))
            source_role = None
            if source_guild:
                source_role = source_guild.get_role(int(role['source_role_id']))
            
            target_role = interaction.guild.get_role(int(role['target_role_id'])) if role['target_role_id'] else None
            
            if source_role and target_role:
                field_value = f"**{source_role.name}** → {target_role.mention}"
                if source_guild:
                    field_value += f"\nСервер: {source_guild.name}"
            else:
                field_value = f"ID роли: {role['source_role_id']}"
                if role['target_role_id']:
                    field_value += f" → Роль ID: {role['target_role_id']}"
            
            embed.add_field(name=f"🔗 Отслеживание #{role['id']}", value=field_value, inline=False)
        
        embed.set_footer(text=f"Всего ролей: {len(tracked_roles)}")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка в list_roles: {e}")
        await interaction.followup.send("❌ Ошибка при загрузке списка ролей", ephemeral=True)

async def sync_all(interaction: discord.Interaction):
    try:
        await interaction.followup.send("🔄 Начинаю синхронизацию...", ephemeral=True)
        guild = interaction.guild
        members = [m for m in guild.members if not m.bot]
        
        processed = 0
        for member in members:
            await role_monitor.sync_user_roles(guild, member.id)
            processed += 1
            if processed % 10 == 0:
                await asyncio.sleep(0.1)
        
        embed = discord.Embed(title="✅ Синхронизация завершена", color=discord.Color.green())
        embed.add_field(name="Обработано пользователей", value=str(processed), inline=True)
        await interaction.edit_original_response(embed=embed)
    except Exception as e:
        logger.error(f"Ошибка в sync_all: {e}")
        await interaction.edit_original_response(content=f"❌ Ошибка: {str(e)[:100]}")

async def stats(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        server_data = db.get_or_create_server(str(guild.id), guild.name)
        
        embed = discord.Embed(title=f"📊 Статистика {guild.name}", color=discord.Color.blue())
        embed.add_field(name="👥 Участники", value=str(guild.member_count), inline=True)
        embed.add_field(name="💬 Каналы", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="👑 Роли сервера", value=str(len(guild.roles)), inline=True)
        
        if server_data:
            tracked_roles = db.get_tracked_roles(server_data['id'])
            banned = db.get_banned_users(server_data['id'])
            settings = db.get_settings(server_data['id'])
            
            embed.add_field(name="📡 Отслеживаемые роли", value=str(len(tracked_roles)), inline=True)
            embed.add_field(name="🔨 Активные баны", value=str(len(banned)), inline=True)
            
            if settings:
                has_news = "✅" if settings.get('news_channel_id') else "❌"
                has_logs = "✅" if settings.get('logs_channel_id') else "❌"
                embed.add_field(name="⚙️ Настройки", value=f"Каналы: {has_news} News, {has_logs} Logs", inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Ошибка в stats: {e}")
        await interaction.followup.send("❌ Ошибка при загрузке статистики", ephemeral=True)

async def unban(interaction: discord.Interaction, user_id: str):
    try:
        if not user_id.isdigit():
            await interaction.edit_original_response(content="❌ ID должен быть числом")
            return
        
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason="Разбан")
        
        server_data = db.get_or_create_server(str(interaction.guild.id), interaction.guild.name)
        if server_data:
            db.unban_user(server_data['id'], user_id)
        
        embed = discord.Embed(title="✅ Пользователь разблокирован", color=discord.Color.green())
        embed.add_field(name="Пользователь", value=f"{user.name} ({user.id})", inline=False)
        await interaction.edit_original_response(content=None, embed=embed)
    except discord.NotFound:
        await interaction.edit_original_response(content="❌ Пользователь не забанен")
    except Exception as e:
        logger.error(f"Ошибка в unban: {e}")
        await interaction.edit_original_response(content=f"❌ Ошибка: {str(e)[:100]}")

# ========== КОМАНДА /SOUZ ==========
@bot.tree.command(name="souz", description="Панель управления ботом")
@app_commands.checks.has_permissions(administrator=True)
async def souz_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    await asyncio.sleep(0.1)
    
    embed = discord.Embed(
        title="🤝 ДОБРО ПОЖАЛОВАТЬ В СОЮЗНЫЙ БОТ!",
        description="Бот для управления доступом на основе ролей с других серверов",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="🔗 ПРИГЛАСИТЬ БОТА НА СЕРВЕРА:",
        value=f"[📋 Пригласить с правами администратора](https://discord.com/api/oauth2/authorize?client_id=1463842572832211061&permissions=8&scope=bot%20applications.commands)\n"
              f"[👁️ Пригласить для просмотра ролей](https://discord.com/api/oauth2/authorize?client_id=1463842572832211061&permissions=268435456&scope=bot%20applications.commands)\n"
              f"**ID бота:** `1463842572832211061`",
        inline=False
    )
    
    view = ControlPanelView()
    await interaction.followup.send(embed=embed, view=view)

# ========== СОБЫТИЯ ==========
@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    try:
        await bot.tree.sync()
        print('✅ Команды синхронизированы')
    except Exception as e:
        print(f'⚠️ Ошибка синхронизации команд: {e}')
    role_monitor.monitor_roles_task.start()
    print('✅ Мониторинг ролей запущен')

@bot.event
async def on_guild_join(guild):
    print(f'✅ Бот добавлен на сервер: {guild.name} (ID: {guild.id})')
    
    # Автоматически создаем таблицы для нового сервера
    db.get_or_create_server(str(guild.id), guild.name)
    db.create_tables()

@bot.event
async def on_guild_remove(guild):
    print(f'❌ Бот удален с сервера: {guild.name} (ID: {guild.id})')

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")