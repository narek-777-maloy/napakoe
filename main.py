"""
Discord All-in-One Bot (discord.py 2.4+)
Monochrome / Akemi Aesthetic Edition (Fully Embedded)
"""
import asyncio, copy, datetime as dt, json, os, re, sqlite3, time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import tasks, commands

TOKEN = os.getenv("DISCORD_TOKEN")
UTC = dt.timezone.utc
BLACK = 0x000000

# GIF-ссылки для аниме-команд
GIFS = {
    "hug": [
        "https://cdn.otakugifs.xyz/gifs/hug/6d915e537c818fa9.gif",
        "https://cdn.otakugifs.xyz/gifs/hug/0d8f88e421d8b1eb.gif"
    ],
    "kiss": [
        "https://cdn.otakugifs.xyz/gifs/kiss/d440c4eb46056f58.gif",
        "https://cdn.otakugifs.xyz/gifs/kiss/99c6d80ba787d40a.gif"
    ],
    "pat": [
        "https://cdn.otakugifs.xyz/gifs/pat/13ec930fd42770f6.gif",
        "https://cdn.otakugifs.xyz/gifs/pat/rzn5K09230.gif"
    ],
    "slap": [
        "https://cdn.otakugifs.xyz/gifs/slap/7882244dc2ba254c.gif"
    ]
}

WELCOME_DEF = {
    "enabled": False, 
    "channel": None, 
    "plain": "Welcome to the Napakoe squad {user.mention}",
    "image": "", 
    "btn1_label": "Roles", "btn1_url": "",
    "btn2_label": "Chat", "btn2_url": "",
    "btn3_label": "Help BizWar", "btn3_url": "",
    "autorole": None
}
GOODBYE_DEF = {"enabled": False, "channel": None, "plain": "**{user.name}** left **{server}**."}

DEFAULTS = {
    "setup_done": False,
    "welcome": WELCOME_DEF,
    "goodbye": GOODBYE_DEF,
    "logs": {"channel": None, "events": {k: True for k in
             ["msg_delete", "msg_edit", "member_join", "member_leave", "roles", "nick",
              "channels", "voice", "bans"]}},
    "backup": {"auto": True, "keep": 5, "last": 0},
    "anticrash": {"enabled": True, "limit": 3, "window": 30, "punish": "ban", "whitelist": []},
    "antilink": {"enabled": False, "mode": "all", "action": "delete", "timeout": 10,
                 "roles": [], "users": [], "channels": [], "domains": []},
    "antibot": {"enabled": True, "whitelist": [], "punish_inviter": False},
    "afk": {"enabled": True, "days": 7, "role": None},
    "apps": {"panel_channel": None, "review_channel": None, "panel_message": None,
             "panel_title": "📨 𝚊𝚙𝚙𝚕𝚒𝚌𝚊𝚝𝚒𝚘𝚗𝚜",
             "panel_text": "𝙲𝚕𝚒𝚌𝚔 𝚝𝚑𝚎 𝚋𝚞𝚝𝚝𝚘𝚗 𝚋𝚎𝚕𝚘𝚠 𝚝𝚘 𝚊𝚙𝚙𝚕𝚢.",
             "types": [{"name": "Helper", "role": None, "questions": ["Ник", "Возраст", "Опыт"]}]},
    "perms": {},
    "fun": {"enabled": True},
}

DEFAULT_PERM = {
    "settings": "administrator", "setup": "administrator", "backup": "administrator",
    "ban": "ban_members", "kick": "kick_members", "timeout": "moderate_members",
    "clear": "manage_messages"
}

SCHEMA = """
create table if not exists settings(guild_id integer primary key, data text);
create table if not exists activity(guild_id integer, user_id integer, ts real, msgs integer default 0, primary key(guild_id,user_id));
create table if not exists marriages(guild_id integer, a integer, b integer, ts real);
create table if not exists apps(id integer primary key autoincrement, guild_id integer, user_id integer, type text, answers text, status text, ts real);
create table if not exists backups(id integer primary key autoincrement, guild_id integer, name text, ts real, data text);
"""

def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out

def dget(d, path):
    for p in path:
        d = d[p]
    return d

def dset(d, path, v):
    for p in path[:-1]:
        d = d[p]
    d[path[-1]] = v

def now():
    return dt.datetime.now(UTC)

def chan(i):
    return f"<#{i}>" if i else "—"

def onoff(v):
    return "✅ Вкл" if v else "❌ Выкл"

# ───────────────────────── Bot Core ─────────────────────────
class Tree(app_commands.CommandTree):
    async def interaction_check(self, i: discord.Interaction):
        if not i.guild or not i.command or not isinstance(i.user, discord.Member):
            return True
        root = (getattr(i.command, "root_parent", None) or i.command).name
        allowed = bot.cfg(i.guild.id)["perms"].get(root)
        p = i.user.guild_permissions
        ok = True
        if p.administrator:
            ok = True
        elif allowed:
            ok = any(r.id in allowed for r in i.user.roles)
        elif root in DEFAULT_PERM:
            ok = getattr(p, DEFAULT_PERM[root])
        if not ok:
            e = discord.Embed(title="⛔ access denied", description="You don't have permission to use this command.", color=BLACK)
            await i.response.send_message(embed=e, ephemeral=True)
        return ok

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, tree_cls=Tree)
        self.db = sqlite3.connect("bot.db", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.cache, self.hits, self.snap, self.written = {}, defaultdict(deque), {}, {}
        self.started = False

    def has_cfg(self, gid):
        return self.db.execute("select 1 from settings where guild_id=?", (gid,)).fetchone() is not None

    def cfg(self, gid):
        if gid not in self.cache:
            row = self.db.execute("select data from settings where guild_id=?", (gid,)).fetchone()
            self.cache[gid] = merge(DEFAULTS, json.loads(row[0]) if row else {})
        return self.cache[gid]

    def save(self, gid):
        self.db.execute("insert or replace into settings values(?,?)", (gid, json.dumps(self.cfg(gid))))
        self.db.commit()

    async def setup_hook(self):
        await self.tree.sync()

bot = Bot()

# ───────────────────────── Webhook Helper ─────────────────────────
async def send_via_webhook(channel: discord.TextChannel, embed: discord.Embed = None, content: str = None, view: discord.ui.View = None):
    try:
        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="Akemi-System")
        if not webhook:
            webhook = await channel.create_webhook(name="Akemi-System")
        
        avatar_url = bot.user.display_avatar.url if bot.user else None
        await webhook.send(
            content=content,
            embed=embed,
            view=view,
            username="Akemi",
            avatar_url=avatar_url
        )
    except discord.HTTPException:
        await channel.send(content=content, embed=embed, view=view)

# ───────────────────────── Приветствия в стиле Akemi ─────────────────────────
class WelcomeView(discord.ui.View):
    def __init__(self, w_cfg):
        super().__init__(timeout=None)
        for i in range(1, 4):
            label = w_cfg.get(f"btn{i}_label")
            url = w_cfg.get(f"btn{i}_url")
            if label and url and url.startswith("http"):
                self.add_item(discord.ui.Button(label=label, url=url, style=discord.ButtonStyle.link))

def build_welcome_msg(w_cfg, m):
    content = w_cfg["plain"].replace("{user.mention}", m.mention).replace("{user.name}", m.display_name).replace("{server}", m.guild.name)
    embed = None
    if w_cfg.get("image"):
        embed = discord.Embed(color=BLACK)
        embed.set_image(url=w_cfg["image"])
    view = WelcomeView(w_cfg)
    return content, embed, view

@bot.listen("on_member_join")
async def _join(m: discord.Member):
    g, c = m.guild, bot.cfg(m.guild.id)
    w = c["welcome"]
    
    if w.get("autorole"):
        r = g.get_role(w["autorole"])
        if r:
            try: await m.add_roles(r, reason="Авто-роль")
            except discord.HTTPException: pass

    if w.get("enabled"):
        ch = g.get_channel(w["channel"] or 0)
        if ch:
            content, embed, view = build_welcome_msg(w, m)
            await send_via_webhook(ch, embed=embed, content=content, view=view)

# ───────────────────────── Учет Активности и Топ ─────────────────────────
def touch_activity(gid, uid):
    t = time.time()
    bot.db.execute("""
        INSERT INTO activity (guild_id, user_id, ts, msgs) VALUES (?, ?, ?, 1)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET ts=?, msgs=msgs+1
    """, (gid, uid, t, t))
    bot.db.commit()

@bot.listen("on_message")
async def _on_message_activity(m: discord.Message):
    if not m.guild or m.author.bot:
        return
    touch_activity(m.guild.id, m.author.id)

@bot.tree.command(name="profile", description="👤 View member profile")
@app_commands.guild_only()
async def profile_cmd(i: discord.Interaction, member: discord.Member = None):
    m = member or i.user
    row = bot.db.execute("SELECT msgs FROM activity WHERE guild_id=? AND user_id=?", (i.guild.id, m.id)).fetchone()
    msgs = row[0] if row else 0
    
    e = discord.Embed(title=f"𝚙𝚛𝚘𝚏𝚒𝚕𝚎 :: {m.display_name}", color=BLACK)
    e.set_thumbnail(url=m.display_avatar.url)
    e.add_field(name="𝚞𝚜𝚎𝚛", value=m.mention, inline=True)
    e.add_field(name="𝚖𝚎𝚜𝚜𝚊𝚐𝚎𝚜", value=f"`{msgs}`", inline=True)
    e.add_field(name="𝚓𝚘𝚒𝚗𝚎𝚍", value=f"<t:{int(m.joined_at.timestamp())}:R>" if m.joined_at else "—", inline=False)
    await i.response.send_message(embed=e)

@bot.tree.command(name="top", description="📊 Top members by messages (Akemi Style)")
@app_commands.guild_only()
async def top_cmd(i: discord.Interaction):
    rows = bot.db.execute(
        "SELECT user_id, msgs FROM activity WHERE guild_id=? ORDER BY msgs DESC LIMIT 10",
        (i.guild.id,)
    ).fetchall()
    
    if not rows:
        e = discord.Embed(title="𝚝𝚘𝚙 𝚊𝚌𝚝𝚒𝚟𝚎 𝚖𝚎𝚖𝚋𝚎𝚛𝚜", description="No activity tracked yet.", color=BLACK)
        return await i.response.send_message(embed=e, ephemeral=True)

    description = []
    for idx, (uid, msgs) in enumerate(rows, start=1):
        description.append(f"`#{idx:02d}` <@{uid}> — **{msgs}** 𝚖𝚜𝚐𝚜")
        
    e = discord.Embed(
        title="𝚝𝚘𝚙 𝚊𝚌𝚝𝚒𝚟𝚎 𝚖𝚎𝚖𝚋𝚎𝚛𝚜",
        description="\n".join(description),
        color=BLACK
    )
    e.set_footer(text="𝚊𝚔𝚎𝚖𝚒 𝚕𝚎𝚊𝚍𝚎𝚛𝚋𝚘𝚊𝚛𝚍 𝚜𝚢𝚜𝚝𝚎𝚖")
    await i.response.send_message(embed=e)

# ───────────────────────── Аниме GIF Взаимодействия (В Эмбедах) ─────────────────────────
def create_action_cmd(name, verb):
    @bot.tree.command(name=name, description=f"{verb.capitalize()} a user with GIF animation")
    @app_commands.guild_only()
    async def _action(i: discord.Interaction, user: discord.Member):
        import random
        gif_url = random.choice(GIFS.get(name, []))
        e = discord.Embed(
            title=f"𝚊𝚌𝚝𝚒𝚘𝚗 :: {name}",
            description=f"✨ {i.user.mention} {verb} {user.mention}",
            color=BLACK
        )
        if gif_url:
            e.set_image(url=gif_url)
        e.set_footer(text="𝚊𝚔𝚎𝚖𝚒 𝚒𝑛𝚝𝑒𝑟𝑎𝑐𝚝𝑖𝑜𝚗 𝚜𝚢𝚜𝚝𝚎𝚖")
        await i.response.send_message(embed=e)

for action_name, verb_text in [("hug", "hugged"), ("kiss", "kissed"), ("pat", "petted"), ("slap", "slapped")]:
    create_action_cmd(action_name, verb_text)

# ───────────────────────── Модерация и служебные (В Эмбедах) ─────────────────────────
@bot.tree.command(name="ban", description="Забанить участника")
@app_commands.guild_only()
async def ban_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    try:
        await user.ban(reason=f"{i.user}: {reason}")
        e = discord.Embed(title="🔨 𝚖𝚘𝚍𝚎𝚛𝚊𝚝𝚒𝚘𝚗 :: 𝚋𝚊𝚗", description=f"Successfully banned {user.mention}.\n**Reason:** {reason}", color=BLACK)
        await i.response.send_message(embed=e)
    except discord.HTTPException as e_err:
        e = discord.Embed(title="⚠️ error", description=f"Failed to ban: {e_err}", color=BLACK)
        await i.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="kick", description="Кикнуть участника")
@app_commands.guild_only()
async def kick_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    try:
        await user.kick(reason=f"{i.user}: {reason}")
        e = discord.Embed(title="👢 𝚖𝚘𝚍𝚎𝚛𝚊𝚝𝚒𝚘𝚗 :: 𝚔𝚒𝚌𝚔", description=f"Successfully kicked {user.mention}.\n**Reason:** {reason}", color=BLACK)
        await i.response.send_message(embed=e)
    except discord.HTTPException as e_err:
        e = discord.Embed(title="⚠️ error", description=f"Failed to kick: {e_err}", color=BLACK)
        await i.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="timeout", description="Выдать мут (таймаут)")
@app_commands.guild_only()
async def timeout_cmd(i: discord.Interaction, user: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "—"):
    try:
        await user.timeout(dt.timedelta(minutes=minutes), reason=f"{i.user}: {reason}")
        e = discord.Embed(title="🔇 𝚖𝚘𝚍𝚎𝚛𝚊𝚝𝚒𝚘𝚗 :: 𝚝𝚒𝚖𝚎𝚘𝚞𝚝", description=f"Muted {user.mention} for **{minutes}** mins.\n**Reason:** {reason}", color=BLACK)
        await i.response.send_message(embed=e)
    except discord.HTTPException as e_err:
        e = discord.Embed(title="⚠️ error", description=f"Failed to timeout: {e_err}", color=BLACK)
        await i.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="clear", description="Удалить сообщения")
@app_commands.guild_only()
async def clear_cmd(i: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await i.response.defer(ephemeral=True)
    deleted = await i.channel.purge(limit=amount)
    e = discord.Embed(title="🧹 𝚖𝚘𝚍𝚎𝚛𝚊𝚝𝚒𝚘𝚗 :: 𝚌𝚕𝚎𝚊𝚛", description=f"Successfully deleted **{len(deleted)}** messages.", color=BLACK)
    await i.followup.send(embed=e, ephemeral=True)

# ───────────────────────── Панель /settings ─────────────────────────
class EditModal(discord.ui.Modal):
    def __init__(self, p, title, specs):
        super().__init__(title=title[:45])
        self.p, self.specs, self.ins = p, specs, []
        for path, label, kind, long in specs:
            cur = dget(p.c, path)
            ti = discord.ui.TextInput(label=label[:45], default=str(cur if cur is not None else "")[:3900],
                                      required=False, max_length=4000 if long else 300,
                                      style=discord.TextStyle.paragraph if long else discord.TextStyle.short)
            self.add_item(ti)
            self.ins.append(ti)

    async def on_submit(self, i):
        for (path, *_), ti in zip(self.specs, self.ins):
            dset(self.p.c, path, ti.value.strip())
        await self.p.refresh(i)

class Toggle(discord.ui.Button):
    def __init__(self, p, label, path, row=None):
        on = dget(p.c, path)
        super().__init__(label=label, emoji="✅" if on else "❌", row=row,
                         style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary)
        self.path = path

    async def callback(self, i):
        v = self.view
        dset(v.c, self.path, not dget(v.c, self.path))
        await v.refresh(i)

class ModalBtn(discord.ui.Button):
    def __init__(self, label, title, specs, row=None):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.title_, self.specs = title, specs

    async def callback(self, i):
        await i.response.send_modal(EditModal(self.view, self.title_, self.specs))

class ActionBtn(discord.ui.Button):
    def __init__(self, label, fn, style=discord.ButtonStyle.secondary, row=None):
        super().__init__(label=label, style=style, row=row)
        self.fn = fn

    async def callback(self, i):
        await self.fn(i, self.view)

class ChanSelect(discord.ui.ChannelSelect):
    def __init__(self, ph, path, row):
        super().__init__(placeholder=ph, row=row, channel_types=[discord.ChannelType.text])
        self.path = path

    async def callback(self, i):
        dset(self.view.c, self.path, self.values[0].id)
        await self.view.refresh(i)

class SectionSelect(discord.ui.Select):
    def __init__(self, cur):
        super().__init__(placeholder="📂 Раздел настроек", row=0, options=[
            discord.SelectOption(label=l, value=k, emoji=e, default=(k == cur)) for k, l, e in SECTION_LIST])

    async def callback(self, i):
        self.view.section = self.values[0]
        await self.view.refresh(i, save=False)

class Panel(discord.ui.View):
    def __init__(self, uid, guild):
        super().__init__(timeout=900)
        self.uid, self.guild, self.section = uid, guild, "welcome"
        self.build()

    @property
    def c(self):
        return bot.cfg(self.guild.id)

    def build(self):
        self.clear_items()
        self.add_item(SectionSelect(self.section))
        for it in SECTIONS[self.section][1](self):
            self.add_item(it)

    def embed(self):
        e = SECTIONS[self.section][0](self)
        e.color = BLACK
        e.set_footer(text=f"{self.guild.name} • Akemi Settings")
        return e

    async def refresh(self, i, save=True):
        if save:
            bot.save(self.guild.id)
        self.build()
        await i.response.edit_message(embed=self.embed(), view=self)

def e_welcome(p):
    c = p.c["welcome"]
    e = discord.Embed(title="👋 Настройки Приветствия (Akemi Style)", color=BLACK)
    e.add_field(name="Статус", value=onoff(c["enabled"]))
    e.add_field(name="Канал", value=chan(c["channel"]))
    e.add_field(name="Текст сообщения", value=f"`{c['plain']}`", inline=False)
    e.add_field(name="Ссылка на баннер", value=c["image"] or "—", inline=False)
    
    btns = []
    for i in range(1, 4):
        lbl, url = c.get(f"btn{i}_label"), c.get(f"btn{i}_url")
        btns.append(f"• **{lbl}**: {url if url else '—'}")
    e.add_field(name="Кнопки-ссылки", value="\n".join(btns), inline=False)
    return e

def i_welcome(p):
    async def test(i, pn):
        content, embed, view = build_welcome_msg(pn.c["welcome"], i.user)
        await i.response.send_message(content=content, embed=embed, view=view, ephemeral=True)

    return [
        Toggle(p, "Включено", ("welcome", "enabled"), 1),
        ActionBtn("Тест", test, discord.ButtonStyle.secondary, 1),
        ModalBtn("Текст и баннер", "Настройка текста", [
            (("welcome", "plain"), "Текст приветствия", "str", True),
            (("welcome", "image"), "URL картинки/баннера", "str", False)
        ], 2),
        ModalBtn("Кнопки (Ссылки)", "Ссылки для кнопок", [
            (("welcome", "btn1_label"), "Кнопка 1 Название", "str", False),
            (("welcome", "btn1_url"), "Кнопка 1 Ссылка (URL)", "str", False),
            (("welcome", "btn2_label"), "Кнопка 2 Название", "str", False),
            (("welcome", "btn2_url"), "Кнопка 2 Ссылка (URL)", "str", False),
            (("welcome", "btn3_label"), "Кнопка 3 Название", "str", False),
        ], 3),
        ModalBtn("Кнопка 3 URL", "Ссылка кнопки 3", [
            (("welcome", "btn3_url"), "Кнопка 3 Ссылка (URL)", "str", False)
        ], 4),
        ChanSelect("Канал приветствия", ("welcome", "channel"), 4)
    ]

SECTION_LIST = [("welcome", "Приветствие", "👋")]
SECTIONS = {"welcome": (e_welcome, i_welcome)}

@bot.tree.command(name="settings", description="Панель настроек бота")
@app_commands.guild_only()
async def settings_cmd(i: discord.Interaction):
    p = Panel(i.user.id, i.guild)
    await i.response.send_message(embed=p.embed(), view=p, ephemeral=True)

# ───────────────────────── Запуск ─────────────────────────
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN environment variable")
    bot.run(TOKEN)
