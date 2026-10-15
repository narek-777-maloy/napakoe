"""
Discord all-in-one бот (discord.py 2.4+)
Приветствия, журнал, бэкап, анти-краш, анти-ссылки, анти-бот, браки,
заявки с кнопками, AFK по неактиву, права команд и панель /settings.
(Модифицирован под чёрную тему эмбедов)
"""
import asyncio, copy, datetime as dt, json, os, re, sqlite3, time
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import tasks, commands

TOKEN = os.getenv("DISCORD_TOKEN")
UTC = dt.timezone.utc
BLACK = 0x000000  # Чёрный цвет для всех эмбедов
DANGER = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                             manage_channels=True, ban_members=True, kick_members=True,
                             manage_webhooks=True).value

WELCOME_DEF = {"enabled": False, "channel": None, "embed": True, "plain": "",
               "title": "Добро пожаловать!",
               "description": "{user.mention}, рады видеть тебя на **{server}**!\nТы {count}-й участник.",
               "color": "#000000", "image": "", "thumbnail": "user", "footer": "", "dm": False, "autorole": None}
GOODBYE_DEF = {**WELCOME_DEF, "title": "До встречи!",
               "description": "**{user.name}** покинул(а) **{server}**.", "color": "#000000"}

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
             "panel_title": "📨 Подача заявок",
             "panel_text": "Нажмите на кнопку ниже, чтобы подать заявку.",
             "types": [
                 {"name": "Хелпер", "role": None, "questions": [
                     "Ваш ник", "Ваш возраст", "Почему хотите стать хелпером?", "Есть ли опыт?"]},
                 {"name": "BW", "role": None, "questions": [
                     "Ваш ник", "Ваш возраст", "Расскажите о себе"]}]},
    "perms": {},
    "fun": {"enabled": True},
}

DEFAULT_PERM = {"settings": "administrator", "setup": "administrator", "backup": "administrator",
                "ban": "ban_members", "kick": "kick_members", "timeout": "moderate_members",
                "clear": "manage_messages"}

SCHEMA = """
create table if not exists settings(guild_id integer primary key, data text);
create table if not exists activity(guild_id integer, user_id integer, ts real, primary key(guild_id,user_id));
create table if not exists marriages(guild_id integer, a integer, b integer, ts real);
create table if not exists apps(id integer primary key autoincrement, guild_id integer, user_id integer,
    type text, answers text, status text, ts real);
create table if not exists backups(id integer primary key autoincrement, guild_id integer, name text,
    ts real, data text);
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


def rolestxt(ids):
    return ", ".join(f"<@&{i}>" for i in ids) or "—"


def userstxt(ids):
    return ", ".join(f"<@{i}>" for i in ids) or "—"


def onoff(v):
    return "✅ Вкл" if v else "❌ Выкл"


def parse_color(s):
    try:
        return int(str(s).lstrip("#"), 16)
    except ValueError:
        return BLACK


# ───────────────────────── Bot ─────────────────────────
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
            await i.response.send_message("⛔ У вас нет доступа к этой команде.", ephemeral=True)
        return ok

    async def on_error(self, i, error):
        if isinstance(error, app_commands.CheckFailure):
            return
        msg = f"⚠️ Ошибка: {error}"
        try:
            if i.response.is_done():
                await i.followup.send(msg, ephemeral=True)
            else:
                await i.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


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
        self.add_dynamic_items(OpenApp, AppOk, AppNo)
        await self.tree.sync()


bot = Bot()


# ───────────────────────── Логи / аудит ─────────────────────────
async def send_log(g, embed, event=None):
    c = bot.cfg(g.id)["logs"]
    if event and not c["events"].get(event, True):
        return
    ch = g.get_channel(c["channel"] or 0)
    if ch:
        embed.timestamp = embed.timestamp or now()
        embed.color = BLACK
        try:
            await ch.send(embed=embed)
        except discord.HTTPException:
            pass


async def audit(g, action, target_id=None, within=10):
    if not g.me.guild_permissions.view_audit_log:
        return None
    try:
        async for e in g.audit_logs(limit=8, action=action):
            if (now() - e.created_at).total_seconds() <= within and \
                    (target_id is None or getattr(e.target, "id", None) == target_id):
                return e
    except discord.HTTPException:
        pass
    return None


def log_embed(title, color=BLACK, **fields):
    e = discord.Embed(title=title, color=BLACK)
    for k, v in fields.items():
        e.add_field(name=k.replace("_", " "), value=str(v)[:1024] or "—", inline=True)
    return e


@bot.listen("on_message_delete")
async def _log_del(m: discord.Message):
    if not m.guild or m.author.bot:
        return
    e = discord.Embed(title="🗑 Сообщение удалено", color=BLACK,
                      description=(m.content or "*без текста*")[:4000])
    e.add_field(name="Автор", value=f"{m.author.mention} (`{m.author.id}`)")
    e.add_field(name="Канал", value=m.channel.mention)
    if m.attachments:
        e.add_field(name="Вложения", value="\n".join(a.filename for a in m.attachments)[:1000])
    ent = await audit(m.guild, discord.AuditLogAction.message_delete, m.author.id)
    if ent and ent.user and ent.user.id != m.author.id:
        e.add_field(name="Удалил", value=ent.user.mention)
    await send_log(m.guild, e, "msg_delete")


@bot.listen("on_message_edit")
async def _log_edit(a: discord.Message, b: discord.Message):
    if not a.guild or a.author.bot or a.content == b.content:
        return
    e = discord.Embed(title="✏️ Сообщение изменено", color=BLACK, url=b.jump_url)
    e.add_field(name="Автор", value=f"{a.author.mention} (`{a.author.id}`)")
    e.add_field(name="Канал", value=a.channel.mention)
    e.add_field(name="Было", value=(a.content or "—")[:1024], inline=False)
    e.add_field(name="Стало", value=(b.content or "—")[:1024], inline=False)
    await send_log(a.guild, e, "msg_edit")


@bot.listen("on_member_update")
async def _log_member(a: discord.Member, b: discord.Member):
    g = b.guild
    if a.roles != b.roles:
        add = [r for r in b.roles if r not in a.roles]
        rem = [r for r in a.roles if r not in b.roles]
        e = discord.Embed(title="🎭 Роли изменены", color=BLACK)
        e.add_field(name="Участник", value=b.mention)
        if add:
            e.add_field(name="Выданы", value=" ".join(r.mention for r in add))
        if rem:
            e.add_field(name="Сняты", value=" ".join(r.mention for r in rem))
        ent = await audit(g, discord.AuditLogAction.member_role_update, b.id)
        if ent and ent.user:
            e.add_field(name="Кем", value=ent.user.mention)
        await send_log(g, e, "roles")
    if a.nick != b.nick:
        e = discord.Embed(title="📝 Ник изменён", color=BLACK)
        e.add_field(name="Участник", value=b.mention)
        e.add_field(name="Было", value=a.nick or "—")
        e.add_field(name="Стало", value=b.nick or "—")
        await send_log(g, e, "nick")


@bot.listen("on_voice_state_update")
async def _log_voice(m, a, b):
    if a.channel == b.channel or m.bot:
        return
    bot.written.pop((m.guild.id, m.id), None)
    touch(m.guild.id, m.id)
    if a.channel is None:
        t = f"🔊 зашёл в {b.channel.mention}"
    elif b.channel is None:
        t = f"🔇 вышел из {a.channel.mention}"
    else:
        t = f"🔁 {a.channel.mention} → {b.channel.mention}"
    await send_log(m.guild, discord.Embed(description=f"{m.mention} {t}", color=BLACK), "voice")


@bot.listen("on_guild_channel_create")
async def _ch_create(ch):
    await send_log(ch.guild, discord.Embed(title="📁 Канал создан", color=BLACK,
                                           description=f"{ch.mention} (`{ch.name}`)"), "channels")


@bot.listen("on_guild_channel_delete")
async def _ch_delete(ch):
    bot.snap[(ch.guild.id, ch.id)] = ser_channel(ch)
    await send_log(ch.guild, discord.Embed(title="🗑 Канал удалён", color=BLACK,
                                           description=f"`{ch.name}`"), "channels")


@bot.listen("on_guild_role_create")
async def _r_create(r):
    await send_log(r.guild, discord.Embed(title="➕ Роль создана", color=BLACK, description=r.mention), "channels")


@bot.listen("on_guild_role_delete")
async def _r_delete(r):
    bot.snap[(r.guild.id, r.id)] = ser_role(r)
    await send_log(r.guild, discord.Embed(title="➖ Роль удалена", color=BLACK, description=f"`{r.name}`"),
                   "channels")


@bot.listen("on_member_ban")
async def _ban(g, u):
    await send_log(g, discord.Embed(title="🔨 Бан", color=BLACK, description=f"{u} (`{u.id}`)"), "bans")


@bot.listen("on_member_unban")
async def _unban(g, u):
    await send_log(g, discord.Embed(title="♻️ Разбан", color=BLACK, description=f"{u} (`{u.id}`)"), "bans")


# ───────────────────────── Приветствия ─────────────────────────
def fmt(text, m):
    return (text.replace("{user.mention}", m.mention).replace("{user.name}", m.display_name)
            .replace("{user}", str(m)).replace("{server}", m.guild.name)
            .replace("{count}", str(m.guild.member_count)))


def build_msg(c, m):
    content = fmt(c["plain"], m) or None
    if not c["embed"]:
        return content or fmt(c["description"], m), None
    e = discord.Embed(title=fmt(c["title"], m)[:256] or None, color=BLACK,
                      description=fmt(c["description"], m)[:4000] or None, timestamp=now())
    if not e.title and not e.description:
        e.description = "\u200b"
    if c["image"]:
        e.set_image(url=c["image"])
    th = c["thumbnail"]
    if th == "user":
        e.set_thumbnail(url=m.display_avatar.url)
    elif th == "server" and m.guild.icon:
        e.set_thumbnail(url=m.guild.icon.url)
    elif th.startswith("http"):
        e.set_thumbnail(url=th)
    if c["footer"]:
        e.set_footer(text=fmt(c["footer"], m)[:2000])
    return content, e


@bot.listen("on_member_join")
async def _join(m: discord.Member):
    g, c = m.guild, bot.cfg(m.guild.id)
    if m.bot:
        return await handle_bot_join(m)
    await send_log(g, log_embed("📥 Вход", BLACK, Участник=f"{m.mention} (`{m.id}`)",
                                Аккаунт=f"<t:{int(m.created_at.timestamp())}:R>"), "member_join")
    w = c["welcome"]
    if w["autorole"]:
        r = g.get_role(w["autorole"])
        if r:
            try:
                await m.add_roles(r, reason="Авто-роль")
            except discord.HTTPException:
                pass
    if w["enabled"]:
        content, e = build_msg(w, m)
        ch = g.get_channel(w["channel"] or 0)
        try:
            if ch:
                await ch.send(content=content, embed=e)
            if w["dm"]:
                await m.send(content=content, embed=e)
        except discord.HTTPException:
            pass


@bot.listen("on_member_remove")
async def _leave(m: discord.Member):
    g, c = m.guild, bot.cfg(m.guild.id)
    await send_log(g, log_embed("📤 Выход", BLACK, Участник=f"{m} (`{m.id}`)"), "member_leave")
    w = c["goodbye"]
    ch = g.get_channel(w["channel"] or 0)
    if w["enabled"] and ch and not m.bot:
        content, e = build_msg(w, m)
        try:
            await ch.send(content=content, embed=e)
        except discord.HTTPException:
            pass


# ───────────────────────── Бэкап ─────────────────────────
def ser_ow(ch):
    out = []
    for t, ow in ch.overwrites.items():
        if isinstance(t, discord.Role):
            a, d = ow.pair()
            out.append({"n": t.name, "e": t.is_default(), "a": a.value, "d": d.value})
    return out


def ser_channel(ch):
    d = {"name": ch.name, "type": ch.type.value, "pos": ch.position,
         "cat": ch.category.name if getattr(ch, "category", None) else None, "ow": ser_ow(ch)}
    if isinstance(ch, discord.TextChannel):
        d.update(topic=ch.topic, nsfw=ch.nsfw, slow=ch.slowmode_delay)
    elif isinstance(ch, discord.VoiceChannel):
        d.update(limit=ch.user_limit)
    return d


def ser_role(r):
    return {"name": r.name, "color": r.color.value, "perms": r.permissions.value, "hoist": r.hoist,
            "mention": r.mentionable, "pos": r.position}


def snapshot(g):
    return {"roles": [ser_role(r) for r in g.roles if not r.is_default() and not r.managed],
            "channels": [ser_channel(c) for c in g.channels if not isinstance(c, discord.Thread)]}


def build_ow(g, lst):
    out = {}
    for o in lst:
        role = g.default_role if o["e"] else discord.utils.get(g.roles, name=o["n"])
        if role:
            out[role] = discord.PermissionOverwrite.from_pair(discord.Permissions(o["a"]),
                                                              discord.Permissions(o["d"]))
    return out


async def make_role(g, d):
    return await g.create_role(name=d["name"], colour=discord.Colour(d["color"]),
                               permissions=discord.Permissions(d["perms"]), hoist=d["hoist"],
                               mentionable=d["mention"], reason="Восстановление")


async def make_channel(g, d):
    ow = build_ow(g, d["ow"])
    cat = discord.utils.get(g.categories, name=d["cat"]) if d.get("cat") else None
    t = d["type"]
    kw = dict(name=d["name"], overwrites=ow, reason="Восстановление")
    if t == 4:
        return await g.create_category(**kw)
    kw["category"] = cat
    if t in (0, 5):
        return await g.create_text_channel(topic=d.get("topic"), nsfw=d.get("nsfw", False),
                                           slowmode_delay=d.get("slow", 0), **kw)
    if t == 2:
        return await g.create_voice_channel(user_limit=d.get("limit", 0), **kw)
    if t == 13:
        return await g.create_stage_channel(**kw)
    if t == 15:
        return await g.create_forum(**kw)


async def restore_snapshot(g, data):
    made = 0
    for d in sorted(data["roles"], key=lambda x: x["pos"]):
        if not discord.utils.get(g.roles, name=d["name"]):
            try:
                await make_role(g, d)
                made += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                pass
    chans = sorted(data["channels"], key=lambda x: (x["type"] != 4, x["pos"]))
    for d in chans:
        exists = any(c.name == d["name"] and c.type.value == d["type"] for c in g.channels)
        if not exists:
            try:
                await make_channel(g, d)
                made += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                pass
    return made


def create_backup(g, name):
    cur = bot.db.execute("insert into backups(guild_id,name,ts,data) values(?,?,?,?)",
                         (g.id, name, time.time(), json.dumps(snapshot(g))))
    bot.db.commit()
    return cur.lastrowid


backup = app_commands.Group(name="backup", description="Бэкап сервера", guild_only=True)


@backup.command(name="create", description="Создать бэкап ролей и каналов")
async def b_create(i: discord.Interaction, name: str = "ручной"):
    await i.response.defer(ephemeral=True)
    bid = create_backup(i.guild, name)
    await i.followup.send(f"💾 Бэкап **#{bid}** создан.")


@backup.command(name="list", description="Список бэкапов")
async def b_list(i: discord.Interaction):
    rows = bot.db.execute("select id,name,ts from backups where guild_id=? order by id desc limit 20",
                          (i.guild.id,)).fetchall()
    txt = "\n".join(f"**#{r[0]}** — {r[1]} — <t:{int(r[2])}:f>" for r in rows) or "Бэкапов нет."
    await i.response.send_message(txt, ephemeral=True)


async def backup_ac(i, cur):
    rows = bot.db.execute("select id,name from backups where guild_id=? order by id desc limit 25",
                          (i.guild.id,)).fetchall()
    return [app_commands.Choice(name=f"#{r[0]} {r[1]}", value=r[0]) for r in rows]


@backup.command(name="restore", description="Восстановить недостающие роли и каналы")
@app_commands.autocomplete(backup_id=backup_ac)
async def b_restore(i: discord.Interaction, backup_id: int):
    row = bot.db.execute("select data from backups where id=? and guild_id=?", (backup_id, i.guild.id)).fetchone()
    if not row:
        return await i.response.send_message("Бэкап не найден.", ephemeral=True)
    await i.response.defer(ephemeral=True)
    n = await restore_snapshot(i.guild, json.loads(row[0]))
    await i.followup.send(f"♻️ Восстановлено объектов: **{n}** (существующие не трогались).")


@backup.command(name="delete", description="Удалить бэкап")
@app_commands.autocomplete(backup_id=backup_ac)
async def b_delete(i: discord.Interaction, backup_id: int):
    bot.db.execute("delete from backups where id=? and guild_id=?", (backup_id, i.guild.id))
    bot.db.commit()
    await i.response.send_message("🗑 Удалено.", ephemeral=True)


bot.tree.add_command(backup)


@tasks.loop(hours=1)
async def auto_backup():
    for g in bot.guilds:
        c = bot.cfg(g.id)["backup"]
        if c["auto"] and time.time() - c["last"] > 86400:
            create_backup(g, "авто")
            c["last"] = time.time()
            bot.db.execute("delete from backups where guild_id=? and name='авто' and id not in "
                           "(select id from backups where guild_id=? and name='авто' order by id desc limit ?)",
                           (g.id, g.id, c["keep"]))
            bot.save(g.id)


# ───────────────────────── Анти-краш ─────────────────────────
CRASH = {discord.AuditLogAction.channel_delete, discord.AuditLogAction.channel_create,
         discord.AuditLogAction.role_delete, discord.AuditLogAction.role_create,
         discord.AuditLogAction.ban, discord.AuditLogAction.kick,
         discord.AuditLogAction.webhook_create}


async def punish(g, user_id, mode, reason):
    m = g.get_member(user_id)
    try:
        if mode == "ban":
            await g.ban(discord.Object(user_id), reason=reason)
        elif mode == "kick" and m:
            await m.kick(reason=reason)
        elif m:
            bad = [r for r in m.roles if not r.is_default() and not r.managed and r.permissions.value & DANGER]
            await m.remove_roles(*bad, reason=reason)
    except discord.HTTPException:
        pass
    await send_log(g, log_embed("🚨 Анти-краш", BLACK, Нарушитель=f"<@{user_id}>",
                                Наказание=mode, Причина=reason))


@bot.listen("on_audit_log_entry_create")
async def _anticrash(entry: discord.AuditLogEntry):
    g = entry.guild
    c = bot.cfg(g.id)["anticrash"]
    u = entry.user
    if not c["enabled"] or entry.action not in CRASH or u is None:
        return
    if u.id in (bot.user.id, g.owner_id) or u.id in c["whitelist"]:
        return
    dq = bot.hits[(g.id, u.id)]
    t = time.time()
    dq.append((t, entry.action, getattr(entry.target, "id", None)))
    while dq and t - dq[0][0] > c["window"]:
        dq.popleft()
    if len(dq) >= c["limit"]:
        acts = list(dq)
        dq.clear()
        await punish(g, u.id, c["punish"], f"Анти-краш: {len(acts)} действий за {c['window']}с")
        await asyncio.sleep(1.5)
        for _, act, tid in acts:
            d = bot.snap.pop((g.id, tid), None)
            try:
                if d and act == discord.AuditLogAction.channel_delete:
                    await make_channel(g, d)
                elif d and act == discord.AuditLogAction.role_delete:
                    await make_role(g, d)
            except discord.HTTPException:
                pass


# ───────────────────────── Анти-бот ─────────────────────────
async def handle_bot_join(m: discord.Member):
    g = m.guild
    c = bot.cfg(g.id)["antibot"]
    if not c["enabled"] or m.id in c["whitelist"]:
        return
    ent = await audit(g, discord.AuditLogAction.bot_add, m.id)
    inviter = ent.user if ent else None
    if inviter and (inviter.id == g.owner_id or inviter.id in bot.cfg(g.id)["anticrash"]["whitelist"]):
        return
    try:
        await m.kick(reason="Анти-бот: бот не в белом списке")
    except discord.HTTPException:
        pass
    await send_log(g, log_embed("🤖 Анти-бот", BLACK, Бот=f"{m} (`{m.id}`)",
                                Добавил=inviter.mention if inviter else "неизвестно", Действие="бот кикнут"))
    if c["punish_inviter"] and inviter:
        await punish(g, inviter.id, bot.cfg(g.id)["anticrash"]["punish"], "Добавил бота вне белого списка")


# ───────────────────────── Анти-ссылки + активность ─────────────────────────
URL_RE = re.compile(r"(?:https?://|www\.)\S+|discord\.gg/\S+|discord(?:app)?\.com/invite/\S+", re.I)


def touch(gid, uid):
    t = time.time()
    if t - bot.written.get((gid, uid), 0) > 60:
        bot.written[(gid, uid)] = t
        bot.db.execute("insert or replace into activity values(?,?,?)", (gid, uid, t))
        bot.db.commit()


async def unafk(m: discord.Member):
    rid = bot.cfg(m.guild.id)["afk"]["role"]
    r = m.guild.get_role(rid) if rid else None
    if r and r in m.roles:
        try:
            await m.remove_roles(r, reason="Снова активен")
        except discord.HTTPException:
            pass


@bot.listen("on_raw_reaction_add")
async def _react(p):
    if p.guild_id and p.member and not p.member.bot:
        touch(p.guild_id, p.user_id)
        await unafk(p.member)


@bot.listen("on_message")
async def _on_message(m: discord.Message):
    if not m.guild or m.author.bot or not isinstance(m.author, discord.Member):
        return
    touch(m.guild.id, m.author.id)
    await unafk(m.author)
    c = bot.cfg(m.guild.id)["antilink"]
    if not c["enabled"] or not m.content:
        return
    a = m.author
    if a.guild_permissions.administrator or a.id in c["users"] or m.channel.id in c["channels"] \
            or any(r.id in c["roles"] for r in a.roles):
        return
    bad = False
    for url in URL_RE.findall(m.content):
        invite = "discord.gg/" in url.lower() or "/invite/" in url.lower()
        dom = re.sub(r"^(?:https?://)?(?:www\.)?", "", url.lower()).split("/")[0]
        if c["mode"] == "invites" and not invite:
            continue
        if any(dom == d or dom.endswith("." + d) for d in c["domains"]):
            continue
        bad = True
    if not bad:
        return
    try:
        await m.delete()
    except discord.HTTPException:
        return
    if c["action"] in ("warn", "timeout"):
        await m.channel.send(f"🔗 {a.mention}, ссылки здесь запрещены.", delete_after=6)
    if c["action"] == "timeout":
        try:
            await a.timeout(dt.timedelta(minutes=c["timeout"]), reason="Анти-ссылки")
        except discord.HTTPException:
            pass
    await send_log(m.guild, log_embed("🔗 Анти-ссылки", BLACK, Автор=a.mention, Канал=m.channel.mention,
                                      Текст=m.content[:500], Действие=c["action"]))


@tasks.loop(hours=1)
async def afk_check():
    for g in bot.guilds:
        c = bot.cfg(g.id)["afk"]
        role = g.get_role(c["role"] or 0)
        if not c["enabled"] or not role:
            continue
        rows = dict(bot.db.execute("select user_id,ts from activity where guild_id=?", (g.id,)).fetchall())
        limit = c["days"] * 86400
        for n, m in enumerate(g.members):
            if m.bot or role in m.roles:
                continue
            last = rows.get(m.id) or (m.joined_at.timestamp() if m.joined_at else time.time())
            if time.time() - last > limit:
                try:
                    await m.add_roles(role, reason=f"Неактив {c['days']} дн.")
                except discord.HTTPException:
                    pass
            if n % 40 == 0:
                await asyncio.sleep(1)


# ───────────────────────── Заявки ─────────────────────────
def can_review(m: discord.Member):
    p = m.guild_permissions
    if p.administrator or p.manage_roles:
        return True
    ids = bot.cfg(m.guild.id)["perms"].get("app_review", [])
    return any(r.id in ids for r in m.roles)


class OpenApp(discord.ui.DynamicItem[discord.ui.Button], template=r"app:open:(?P<i>\d+)"):
    def __init__(self, idx, label="Подать заявку"):
        super().__init__(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary,
                                           custom_id=f"app:open:{idx}", emoji="📨"))
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["i"]), item.label or "Подать заявку")

    async def callback(self, i: discord.Interaction):
        types = bot.cfg(i.guild.id)["apps"]["types"]
        if self.idx >= len(types):
            return await i.response.send_message("Этот тип заявок больше не существует.", ephemeral=True)
        t = types[self.idx]
        pend = bot.db.execute("select 1 from apps where guild_id=? and user_id=? and type=? and status='pending'",
                              (i.guild.id, i.user.id, t["name"])).fetchone()
        if pend:
            return await i.response.send_message("⏳ Ваша заявка уже на рассмотрении.", ephemeral=True)
        await i.response.send_modal(ApplyModal(t))


class ApplyModal(discord.ui.Modal):
    def __init__(self, t):
        super().__init__(title=f"Заявка: {t['name']}"[:45])
        self.t, self.inputs = t, []
        for q in t["questions"][:5]:
            ti = discord.ui.TextInput(label=q[:45], style=discord.TextStyle.paragraph, max_length=1000)
            self.add_item(ti)
            self.inputs.append(ti)

    async def on_submit(self, i: discord.Interaction):
        a = bot.cfg(i.guild.id)["apps"]
        rev = i.guild.get_channel(a["review_channel"] or 0)
        if not rev:
            return await i.response.send_message("Канал рассмотрения не настроен.", ephemeral=True)
        ans = [(q, ti.value) for q, ti in zip(self.t["questions"], self.inputs)]
        cur = bot.db.execute("insert into apps(guild_id,user_id,type,answers,status,ts) values(?,?,?,?,?,?)",
                             (i.guild.id, i.user.id, self.t["name"], json.dumps(ans), "pending", time.time()))
        bot.db.commit()
        app_id = cur.lastrowid
        e = discord.Embed(title=f"📨 Заявка #{app_id}: {self.t['name']}", color=BLACK, timestamp=now(),
                          description=f"{i.user.mention} (`{i.user.id}`)")
        e.set_author(name=str(i.user), icon_url=i.user.display_avatar.url)
        for q, v in ans:
            e.add_field(name=q[:256], value=v[:1024] or "—", inline=False)
        v = discord.ui.View(timeout=None)
        v.add_item(AppOk(app_id))
        v.add_item(AppNo(app_id))
        await rev.send(embed=e, view=v)
        await i.response.send_message("✅ Заявка отправлена, ожидайте решения.", ephemeral=True)


async def finish_app(i: discord.Interaction, app_id, status, reason=""):
    row = bot.db.execute("select user_id,type,status from apps where id=?", (app_id,)).fetchone()
    if not row or row[2] != "pending":
        return await i.response.send_message("Заявка уже рассмотрена.", ephemeral=True)
    bot.db.execute("update apps set status=? where id=?", (status, app_id))
    bot.db.commit()
    ok = status == "accepted"
    e = i.message.embeds[0]
    e.color = BLACK
    e.add_field(name="Решение", value=("✅ Одобрена" if ok else "❌ Отклонена") + (f"\nПричина: {reason}" if reason else ""),
                inline=False)
    e.set_footer(text=f"Рассмотрел: {i.user}")
    await i.response.edit_message(embed=e, view=None)
    m = i.guild.get_member(row[0])
    if not m:
        return
    t = next((x for x in bot.cfg(i.guild.id)["apps"]["types"] if x["name"] == row[1]), None)
    try:
        if ok and t and t.get("role") and i.guild.get_role(t["role"]):
            await m.add_roles(i.guild.get_role(t["role"]), reason="Заявка одобрена")
        await m.send(f"{'✅ Ваша заявка «' + row[1] + '» одобрена!' if ok else '❌ Ваша заявка «' + row[1] + '» отклонена.'}"
                     + (f"\nПричина: {reason}" if reason else "") + f"\nСервер: {i.guild.name}")
    except discord.HTTPException:
        pass


class AppOk(discord.ui.DynamicItem[discord.ui.Button], template=r"app:ok:(?P<id>\d+)"):
    def __init__(self, app_id):
        super().__init__(discord.ui.Button(label="Одобрить", style=discord.ButtonStyle.success,
                                           custom_id=f"app:ok:{app_id}", emoji="✅"))
        self.app_id = app_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, i):
        if not can_review(i.user):
            return await i.response.send_message("⛔ Нет прав рассматривать заявки.", ephemeral=True)
        await finish_app(i, self.app_id, "accepted")


class RejectModal(discord.ui.Modal, title="Отклонить заявку"):
    reason = discord.ui.TextInput(label="Причина (необязательно)", required=False,
                                  style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, app_id):
        super().__init__()
        self.app_id = app_id

    async def on_submit(self, i):
        await finish_app(i, self.app_id, "rejected", self.reason.value)


class AppNo(discord.ui.DynamicItem[discord.ui.Button], template=r"app:no:(?P<id>\d+)"):
    def __init__(self, app_id):
        super().__init__(discord.ui.Button(label="Отклонить", style=discord.ButtonStyle.danger,
                                           custom_id=f"app:no:{app_id}", emoji="❌"))
        self.app_id = app_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, i):
        if not can_review(i.user):
            return await i.response.send_message("⛔ Нет прав рассматривать заявки.", ephemeral=True)
        await i.response.send_modal(RejectModal(self.app_id))


async def update_panel(g, force=False):
    a = bot.cfg(g.id)["apps"]
    ch = g.get_channel(a["panel_channel"] or 0)
    if not ch or not a["types"]:
        return
    e = discord.Embed(title=a["panel_title"], description=a["panel_text"], color=BLACK)
    v = discord.ui.View(timeout=None)
    for idx, t in enumerate(a["types"][:25]):
        v.add_item(OpenApp(idx, t["name"]))
    msg = None
    if a["panel_message"]:
        try:
            msg = await ch.fetch_message(a["panel_message"])
        except discord.HTTPException:
            msg = None
    try:
        if msg:
            if force:
                await msg.edit(embed=e, view=v)
        else:
            a["panel_message"] = (await ch.send(embed=e, view=v)).id
            bot.save(g.id)
    except discord.HTTPException:
        pass


@bot.listen("on_raw_message_delete")
async def _panel_deleted(p):
    if p.guild_id and bot.cfg(p.guild_id)["apps"]["panel_message"] == p.message_id:
        g = bot.get_guild(p.guild_id)
        bot.cfg(p.guild_id)["apps"]["panel_message"] = None
        if g:
            await update_panel(g)


@tasks.loop(minutes=5)
async def panel_guard():
    for g in bot.guilds:
        await update_panel(g)


# ───────────────────────── Авто-настройка ─────────────────────────
async def setup_guild(g: discord.Guild, force=False):
    c = bot.cfg(g.id)
    if c["setup_done"] and not force:
        return False
    me = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
    priv = {g.default_role: discord.PermissionOverwrite(view_channel=False), g.me: me}
    ro = {g.default_role: discord.PermissionOverwrite(send_messages=False), g.me: me}
    try:
        cat = await g.create_category("🤖 Бот", reason="Авто-настройка")
        logs = await g.create_text_channel("логи", category=cat, overwrites=priv)
        welcome = await g.create_text_channel("приветствие", category=cat, overwrites=ro)
        panel = await g.create_text_channel("подать-заявку", category=cat, overwrites=ro)
        review = await g.create_text_channel("заявки-рассмотрение", category=cat, overwrites=priv)
        afk = await g.create_role(name="AFK", colour=discord.Colour(0x000000), reason="Авто-настройка")
    except discord.HTTPException:
        return False
    c["logs"]["channel"] = logs.id
    c["welcome"]["channel"] = welcome.id
    c["goodbye"]["channel"] = welcome.id
    c["apps"]["panel_channel"], c["apps"]["review_channel"] = panel.id, review.id
    c["afk"]["role"] = afk.id
    c["setup_done"] = True
    bot.save(g.id)
    await update_panel(g, force=True)
    await logs.send("✅ Бот настроен автоматически. Все параметры — в **/settings**.")
    return True


@bot.listen("on_guild_join")
async def _guild_join(g):
    await setup_guild(g)


# ───────────────────────── Панель /settings ─────────────────────────
class EditModal(discord.ui.Modal):
    def __init__(self, p, title, specs):
        super().__init__(title=title[:45])
        self.p, self.specs, self.ins = p, specs, []
        for path, label, kind, long in specs:
            cur = dget(p.c, path)
            if kind in ("list", "ids"):
                cur = ", ".join(map(str, cur))
            ti = discord.ui.TextInput(label=label[:45], default=str(cur if cur is not None else "")[:3900],
                                      required=False, max_length=4000 if long else 300,
                                      style=discord.TextStyle.paragraph if long else discord.TextStyle.short)
            self.add_item(ti)
            self.ins.append(ti)

    async def on_submit(self, i):
        vals = []
        for (path, label, kind, _), ti in zip(self.specs, self.ins):
            v = ti.value.strip()
            try:
                if kind == "int":
                    v = int(v)
                elif kind == "ids":
                    v = [int(x) for x in re.split(r"[,\s]+", v) if x]
                elif kind == "list":
                    v = [x.strip().lower() for x in re.split(r"[,\n]+", v) if x.strip()]
            except ValueError:
                return await i.response.send_message(f"«{label}»: неверный формат (нужны числа).", ephemeral=True)
            vals.append(v)
        for (path, *_), v in zip(self.specs, vals):
            dset(self.p.c, path, v)
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


class Cycle(discord.ui.Button):
    def __init__(self, p, label, path, options, row=None):
        super().__init__(label=f"{label}: {dget(p.c, path)}", style=discord.ButtonStyle.primary, row=row)
        self.path, self.options = path, options

    async def callback(self, i):
        v = self.view
        cur = dget(v.c, self.path)
        dset(v.c, self.path, self.options[(self.options.index(cur) + 1) % len(self.options)])
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


def clear_btn(label, pairs, row):
    async def fn(i, p):
        for path, val in pairs:
            dset(p.c, path, val)
        await p.refresh(i)
    return ActionBtn(label, fn, discord.ButtonStyle.danger, row)


class ChanSelect(discord.ui.ChannelSelect):
    def __init__(self, ph, path, row, after=None):
        super().__init__(placeholder=ph, row=row,
                         channel_types=[discord.ChannelType.text, discord.ChannelType.news])
        self.path, self.after = path, after

    async def callback(self, i):
        v = self.view
        dset(v.c, self.path, self.values[0].id)
        await v.refresh(i)
        if self.after:
            await self.after(v)


class RoleSel(discord.ui.RoleSelect):
    def __init__(self, ph, path, row, single=False, after=None):
        super().__init__(placeholder=ph, row=row, min_values=1, max_values=1 if single else 25)
        self.path, self.single, self.after = path, single, after

    async def callback(self, i):
        v = self.view
        ids = [r.id for r in self.values]
        dset(v.c, self.path, ids[0] if self.single else ids)
        await v.refresh(i)
        if self.after:
            await self.after(v)


class UserSel(discord.ui.UserSelect):
    def __init__(self, ph, path, row):
        super().__init__(placeholder=ph, row=row, min_values=1, max_values=25)
        self.path = path

    async def callback(self, i):
        v = self.view
        dset(v.c, self.path, [u.id for u in self.values])
        await v.refresh(i)


class SectionSelect(discord.ui.Select):
    def __init__(self, cur):
        super().__init__(placeholder="📂 Раздел настроек", row=0, options=[
            discord.SelectOption(label=l, value=k, emoji=e, default=(k == cur)) for k, l, e in SECTION_LIST])

    async def callback(self, i):
        v = self.view
        v.section, v.state = self.values[0], {}
        await v.refresh(i, save=False)


class Panel(discord.ui.View):
    def __init__(self, uid, guild):
        super().__init__(timeout=900)
        self.uid, self.guild, self.section, self.state, self.origin = uid, guild, "main", {}, None
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
        e.set_footer(text=f"{self.guild.name} • настройки сохраняются автоматически")
        return e

    async def refresh(self, i, save=True):
        if save:
            bot.save(self.guild.id)
        self.build()
        await i.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, i):
        if i.user.id != self.uid:
            await i.response.send_message("Это чужая панель — откройте свою через /settings", ephemeral=True)
            return False
        return True


def emb(title, desc=""):
    return discord.Embed(title=title, description=desc, color=BLACK)


# ── разделы ──
def e_main(p):
    c = p.c
    e = emb("⚙️ Панель управления", "Выберите раздел в меню ниже.")
    e.add_field(name="Модули", inline=False, value="\n".join([
        f"👋 Приветствие: {onoff(c['welcome']['enabled'])}", f"🚪 Прощание: {onoff(c['goodbye']['enabled'])}",
        f"📜 Журнал: {chan(c['logs']['channel'])}", f"🛡️ Анти-краш: {onoff(c['anticrash']['enabled'])}",
        f"🔗 Анти-ссылки: {onoff(c['antilink']['enabled'])}", f"🤖 Анти-бот: {onoff(c['antibot']['enabled'])}",
        f"💤 AFK: {onoff(c['afk']['enabled'])}", f"🎉 Развлечения: {onoff(c['fun']['enabled'])}"]))
    return e


def sec_welcome(key):
    def e_(p):
        c = p.c[key]
        e = emb("👋 Приветствие" if key == "welcome" else "🚪 Прощание")
        e.add_field(name="Статус", value=onoff(c["enabled"]))
        e.add_field(name="Канал", value=chan(c["channel"]))
        e.add_field(name="Формат", value="Embed" if c["embed"] else "Обычный текст")
        e.add_field(name="Заголовок", value=c["title"] or "—", inline=False)
        e.add_field(name="Описание", value=c["description"][:1000] or "—", inline=False)
        e.add_field(name="Цвет / картинка / миниатюра / футер", inline=False,
                    value=f"`{c['color']}` • {c['image'] or '—'} • `{c['thumbnail']}` • {c['footer'] or '—'}")
        if key == "welcome":
            e.add_field(name="Авто-роль", value=f"<@&{c['autorole']}>" if c["autorole"] else "—")
            e.add_field(name="Дублировать в ЛС", value=onoff(c["dm"]))
        e.add_field(name="Плейсхолдеры", inline=False,
                    value="`{user.mention}` `{user.name}` `{user}` `{server}` `{count}`\n"
                          "Миниатюра: `user` / `server` / `none` / ссылка")
        return e

    def items(p):
        async def test(i, pn):
            content, e = build_msg(pn.c[key], i.user)
            await i.response.send_message(content=content, embed=e, ephemeral=True)
        r = [Toggle(p, "Включено", (key, "enabled"), 1), Toggle(p, "Embed", (key, "embed"), 1),
             ActionBtn("Тест", test, discord.ButtonStyle.secondary, 1),
             ModalBtn("Embed", "Настройка embed", [((key, "title"), "Заголовок", "str", False),
                                                  ((key, "description"), "Описание", "str", True),
                                                  ((key, "color"), "Цвет (#hex)", "str", False),
                                                  ((key, "image"), "Ссылка на картинку", "str", False),
                                                  ((key, "footer"), "Футер", "str", False)], 1),
             ModalBtn("Текст/вид", "Текст и миниатюра", [((key, "plain"), "Обычный текст над embed", "str", True),
                                                        ((key, "thumbnail"), "Миниатюра", "str", False)], 1),
             ChanSelect("Канал сообщений", (key, "channel"), 2)]
        if key == "welcome":
            r += [RoleSel("Авто-роль новичкам", (key, "autorole"), 3, single=True),
                  Toggle(p, "Дублировать в ЛС", (key, "dm"), 4),
                  clear_btn("Убрать авто-роль", [((key, "autorole"), None)], 4)]
        return r
    return e_, items


LOG_NAMES = {"msg_delete": "Удаление", "msg_edit": "Правки", "member_join": "Входы", "member_leave": "Выходы",
             "roles": "Роли", "nick": "Ники", "channels": "Каналы/роли", "voice": "Голос", "bans": "Баны"}


def e_logs(p):
    c = p.c["logs"]
    e = emb("📜 Журнал действий", f"Канал: {chan(c['channel'])}")
    e.add_field(name="События", value="\n".join(f"{onoff(c['events'][k])} — {n}" for k, n in LOG_NAMES.items()))
    return e


def i_logs(p):
    r = [Toggle(p, n, ("logs", "events", k), 1 if n_ < 5 else 2) for n_, (k, n) in enumerate(LOG_NAMES.items())]
    return r + [ChanSelect("Канал журнала", ("logs", "channel"), 3)]


def e_backup(p):
    c = p.c["backup"]
    rows = bot.db.execute("select id,name,ts from backups where guild_id=? order by id desc limit 8",
                          (p.guild.id,)).fetchall()
    e = emb("💾 Бэкап сервера", "Сохраняет роли и каналы с правами. Восстановление: `/backup restore` "
                                 "(возвращает только недостающее, ничего не удаляя).")
    e.add_field(name="Авто-бэкап раз в сутки", value=f"{onoff(c['auto'])} • хранить {c['keep']}")
    e.add_field(name="Последние", value="\n".join(f"#{r[0]} {r[1]} — <t:{int(r[2])}:R>" for r in rows) or "—",
                inline=False)
    return e


def i_backup(p):
    async def now_(i, pn):
        create_backup(pn.guild, "ручной")
        await pn.refresh(i, save=False)
    return [Toggle(p, "Авто-бэкап", ("backup", "auto"), 1),
            ActionBtn("Создать сейчас", now_, discord.ButtonStyle.success, 1),
            ModalBtn("Сколько хранить", "Хранить авто-бэкапов", [(("backup", "keep"), "Количество", "int", False)], 1)]


def e_crash(p):
    c = p.c["anticrash"]
    e = emb("🛡️ Анти-краш", "Следит за массовым удалением/созданием каналов и ролей, банами, киками, вебхуками. "
                             "При превышении — наказывает и восстанавливает удалённые каналы/роли.")
    e.add_field(name="Статус", value=onoff(c["enabled"]))
    e.add_field(name="Лимит", value=f"{c['limit']} действий / {c['window']} сек")
    e.add_field(name="Наказание", value=c["punish"])
    e.add_field(name="Белый список", value=userstxt(c["whitelist"]), inline=False)
    return e


def i_crash(p):
    return [Toggle(p, "Включено", ("anticrash", "enabled"), 1),
            Cycle(p, "Наказание", ("anticrash", "punish"), ["ban", "kick", "strip"], 1),
            ModalBtn("Лимиты", "Лимиты анти-краша", [(("anticrash", "limit"), "Действий", "int", False),
                                                    (("anticrash", "window"), "За сколько секунд", "int", False)], 1),
            clear_btn("Очистить список", [(("anticrash", "whitelist"), [])], 1),
            UserSel("Белый список (не наказываются)", ("anticrash", "whitelist"), 2)]


def e_link(p):
    c = p.c["antilink"]
    e = emb("🔗 Анти-ссылки")
    e.add_field(name="Статус", value=onoff(c["enabled"]))
    e.add_field(name="Что блокировать", value="все ссылки" if c["mode"] == "all" else "только инвайты")
    e.add_field(name="Действие", value=f"{c['action']}" + (f" ({c['timeout']} мин)" if c["action"] == "timeout" else ""))
    e.add_field(name="Разрешено ролям", value=rolestxt(c["roles"]), inline=False)
    e.add_field(name="Разрешено участникам", value=userstxt(c["users"]), inline=False)
    e.add_field(name="Разрешённые каналы", value=" ".join(f"<#{x}>" for x in c["channels"]) or "—", inline=False)
    e.add_field(name="Разрешённые домены", value=", ".join(c["domains"]) or "—", inline=False)
    return e


def i_link(p):
    class LinkChan(discord.ui.ChannelSelect):
        def __init__(self):
            super().__init__(placeholder="Каналы, где ссылки можно", row=4, min_values=1, max_values=25,
                             channel_types=[discord.ChannelType.text, discord.ChannelType.news])

        async def callback(self, i):
            self.view.c["antilink"]["channels"] = [c.id for c in self.values]
            await self.view.refresh(i)
    return [Toggle(p, "Включено", ("antilink", "enabled"), 1),
            Cycle(p, "Режим", ("antilink", "mode"), ["all", "invites"], 1),
            Cycle(p, "Действие", ("antilink", "action"), ["delete", "warn", "timeout"], 1),
            ModalBtn("Домены/мут", "Домены и таймаут", [(("antilink", "domains"), "Разрешённые домены (через запятую)", "list", True),
                                                      (("antilink", "timeout"), "Минут мута", "int", False)], 1),
            clear_btn("Сбросить списки", [(("antilink", "roles"), []), (("antilink", "users"), []),
                                          (("antilink", "channels"), []), (("antilink", "domains"), [])], 1),
            RoleSel("Роли, которым можно", ("antilink", "roles"), 2),
            UserSel("Участники, которым можно", ("antilink", "users"), 3), LinkChan()]


def e_bot(p):
    c = p.c["antibot"]
    e = emb("🤖 Анти-бот", "Любой бот не из белого списка кикается сразу после добавления.")
    e.add_field(name="Статус", value=onoff(c["enabled"]))
    e.add_field(name="Наказывать добавившего", value=onoff(c["punish_inviter"]))
    e.add_field(name="Белый список ботов", value=userstxt(c["whitelist"]), inline=False)
    return e


def i_bot(p):
    return [Toggle(p, "Включено", ("antibot", "enabled"), 1),
            Toggle(p, "Наказывать добавившего", ("antibot", "punish_inviter"), 1),
            clear_btn("Очистить список", [(("antibot", "whitelist"), [])], 1),
            UserSel("Разрешённые боты (выберите ботов)", ("antibot", "whitelist"), 2)]


def e_apps(p):
    a = p.c["apps"]
    e = emb("📨 Заявки", "Кнопки на панели работают всегда: переживают перезапуск, а если сообщение удалят — "
                         "бот отправит его заново.")
    e.add_field(name="Канал панели", value=chan(a["panel_channel"]))
    e.add_field(name="Канал рассмотрения", value=chan(a["review_channel"]))
    e.add_field(name="Типы заявок", inline=False, value="\n".join(
        f"**{t['name']}** — вопросов: {len(t['questions'])}, роль: " + (f"<@&{t['role']}>" if t['role'] else "—")
        for t in a["types"]) or "—")
    e.add_field(name="Кто рассматривает", value="Админы, «Управление ролями» и роли из раздела «Права» (заявки)")
    return e


class TypeModal(discord.ui.Modal):
    def __init__(self, p, idx=None):
        super().__init__(title="Тип заявки")
        self.p, self.idx = p, idx
        t = p.c["apps"]["types"][idx] if idx is not None else {"name": "", "questions": [], "role": None}
        self.n = discord.ui.TextInput(label="Название кнопки", default=t["name"], max_length=40)
        self.q = discord.ui.TextInput(label="Вопросы (каждый с новой строки, до 5)", default="\n".join(t["questions"]),
                                      style=discord.TextStyle.paragraph, max_length=1500)
        self.r = discord.ui.TextInput(label="ID роли при одобрении (необязательно)", required=False,
                                      default=str(t["role"] or ""))
        for x in (self.n, self.q, self.r):
            self.add_item(x)

    async def on_submit(self, i):
        qs = [x.strip()[:45] for x in self.q.value.splitlines() if x.strip()][:5]
        if not qs:
            return await i.response.send_message("Нужен хотя бы один вопрос.", ephemeral=True)
        role = int(self.r.value) if self.r.value.strip().isdigit() else None
        t = {"name": self.n.value.strip(), "questions": qs, "role": role}
        types = self.p.c["apps"]["types"]
        if self.idx is None:
            types.append(t)
        else:
            types[self.idx] = t
        await finish_types(self.p, i, "✅ Сохранено.")


async def finish_types(p, i, text):
    bot.save(p.guild.id)
    await update_panel(p.guild, force=True)
    p.build()
    try:
        await p.origin.edit_original_response(embed=p.embed(), view=p)
    except discord.HTTPException:
        pass
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)


class TypeActions(discord.ui.View):
    def __init__(self, p, idx):
        super().__init__(timeout=120)
        self.p, self.idx = p, idx

    @discord.ui.button(label="Изменить", style=discord.ButtonStyle.primary)
    async def edit(self, i, b):
        await i.response.send_modal(TypeModal(self.p, self.idx))

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger)
    async def delete(self, i, b):
        self.p.c["apps"]["types"].pop(self.idx)
        await finish_types(self.p, i, "🗑 Удалено.")


class TypeSelect(discord.ui.Select):
    def __init__(self, p):
        super().__init__(placeholder="Изменить / удалить тип заявки", row=2, options=[
            discord.SelectOption(label=t["name"][:100], value=str(n)) for n, t in enumerate(p.c["apps"]["types"][:25])])

    async def callback(self, i):
        idx = int(self.values[0])
        await i.response.send_message(f"Тип: **{self.view.c['apps']['types'][idx]['name']}**",
                                      view=TypeActions(self.view, idx), ephemeral=True)


def i_apps(p):
    async def publish(i, pn):
        await i.response.defer()
        pn.c["apps"]["panel_message"] = None if not pn.c["apps"]["panel_message"] else pn.c["apps"]["panel_message"]
        await update_panel(pn.guild, force=True)
        await i.followup.send("📨 Панель опубликована/обновлена.", ephemeral=True)

    async def add(i, pn):
        await i.response.send_modal(TypeModal(pn))

    async def after(v):
        v.c["apps"]["panel_message"] = None
        await update_panel(v.guild)

    r = [ActionBtn("Опубликовать панель", publish, discord.ButtonStyle.success, 1),
         ActionBtn("+ Новый тип", add, discord.ButtonStyle.primary, 1),
         ModalBtn("Тексты панели", "Тексты панели", [(("apps", "panel_title"), "Заголовок", "str", False),
                                                    (("apps", "panel_text"), "Описание", "str", True)], 1)]
    if p.c["apps"]["types"]:
        r.append(TypeSelect(p))
    return r + [ChanSelect("Канал с кнопкой подачи", ("apps", "panel_channel"), 3, after),
                ChanSelect("Канал, куда приходят заявки", ("apps", "review_channel"), 4)]


def e_afk(p):
    c = p.c["afk"]
    e = emb("💤 Проверка неактива", "Если участник не писал, не сидел в войсе и не ставил реакции N дней — "
                                    "он получает роль AFK. Как только проявит активность — роль снимается.")
    e.add_field(name="Статус", value=onoff(c["enabled"]))
    e.add_field(name="Порог", value=f"{c['days']} дн.")
    e.add_field(name="Роль AFK", value=f"<@&{c['role']}>" if c["role"] else "—")
    return e


def i_afk(p):
    return [Toggle(p, "Включено", ("afk", "enabled"), 1),
            ModalBtn("Сколько дней", "Порог неактива", [(("afk", "days"), "Дней", "int", False)], 1),
            RoleSel("Роль AFK", ("afk", "role"), 2, single=True)]


def perm_names():
    return sorted({c.name for c in bot.tree.get_commands()}) + ["app_review"]


def e_perms(p):
    sel = p.state.get("cmd", "ban")
    e = emb("🔐 Права команд", "Выберите команду и роли, которым она разрешена. Если роли не заданы — действуют "
                               "права по умолчанию. Админы могут всё. `app_review` — кто рассматривает заявки.")
    e.add_field(name="Выбрано", value=f"`/{sel}`", inline=False)
    e.add_field(name="Разрешено ролям", value=rolestxt(p.c["perms"].get(sel, [])) if p.c["perms"].get(sel) else "по умолчанию")
    custom = [f"`{k}`: {rolestxt(v)}" for k, v in p.c["perms"].items() if v]
    e.add_field(name="Все настроенные", value="\n".join(custom) or "—", inline=False)
    return e


def i_perms(p):
    sel = p.state.get("cmd", "ban")

    class CmdSel(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="Команда", row=1, options=[
                discord.SelectOption(label=n, value=n, default=(n == sel)) for n in perm_names()[:25]])

        async def callback(self, i):
            self.view.state["cmd"] = self.values[0]
            await self.view.refresh(i, save=False)

    class PRole(discord.ui.RoleSelect):
        def __init__(self):
            super().__init__(placeholder="Роли с доступом", row=2, min_values=1, max_values=25)

        async def callback(self, i):
            self.view.c["perms"][sel] = [r.id for r in self.values]
            await self.view.refresh(i)

    async def reset(i, pn):
        pn.c["perms"].pop(sel, None)
        await pn.refresh(i)
    return [CmdSel(), PRole(), ActionBtn("Сбросить к умолчанию", reset, discord.ButtonStyle.danger, 3)]


def e_fun(p):
    e = emb("🎉 Развлечения", "Команды: `/marry` `/divorce` `/marriage` `/hug` `/kiss` `/pat` `/slap`")
    e.add_field(name="Статус", value=onoff(p.c["fun"]["enabled"]))
    return e


SECTION_LIST = [("main", "Обзор", "🏠"), ("welcome", "Приветствие", "👋"), ("goodbye", "Прощание", "🚪"),
                ("logs", "Журнал действий", "📜"), ("backup", "Бэкап", "💾"), ("anticrash", "Анти-краш", "🛡️"),
                ("antilink", "Анти-ссылки", "🔗"), ("antibot", "Анти-бот", "🤖"), ("apps", "Заявки", "📨"),
                ("afk", "Неактив / AFK", "💤"), ("perms", "Права команд", "🔐"), ("fun", "Развлечения", "🎉")]
SECTIONS = {"main": (e_main, lambda p: []),
            "welcome": sec_welcome("welcome"), "goodbye": sec_welcome("goodbye"),
            "logs": (e_logs, i_logs), "backup": (e_backup, i_backup), "anticrash": (e_crash, i_crash),
            "antilink": (e_link, i_link), "antibot": (e_bot, i_bot), "apps": (e_apps, i_apps),
            "afk": (e_afk, i_afk), "perms": (e_perms, i_perms),
            "fun": (e_fun, lambda p: [Toggle(p, "Включено", ("fun", "enabled"), 1)])}


@bot.tree.command(name="settings", description="Панель настроек бота")
@app_commands.guild_only()
async def settings_cmd(i: discord.Interaction):
    p = Panel(i.user.id, i.guild)
    p.origin = i
    await i.response.send_message(embed=p.embed(), view=p, ephemeral=True)


@bot.tree.command(name="setup", description="Автоматически создать каналы и роли бота")
@app_commands.guild_only()
async def setup_cmd(i: discord.Interaction, recreate: bool = False):
    await i.response.defer(ephemeral=True)
    ok = await setup_guild(i.guild, force=recreate)
    await i.followup.send("✅ Готово! Откройте /settings." if ok else
                          "Уже настроено (используйте recreate=True) или не хватает прав.")


# ───────────────────────── Модерация ─────────────────────────
def hier_ok(i, target):
    return i.user.id == i.guild.owner_id or target.top_role < i.user.top_role


@bot.tree.command(name="ban", description="Забанить участника")
@app_commands.guild_only()
async def ban_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    if not hier_ok(i, user):
        return await i.response.send_message("Роль цели не ниже вашей.", ephemeral=True)
    try:
        await user.ban(reason=f"{i.user}: {reason}")
        await i.response.send_message(f"🔨 {user} забанен. Причина: {reason}")
    except discord.HTTPException as e:
        await i.response.send_message(f"Не удалось: {e}", ephemeral=True)


@bot.tree.command(name="kick", description="Кикнуть участника")
@app_commands.guild_only()
async def kick_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    if not hier_ok(i, user):
        return await i.response.send_message("Роль цели не ниже вашей.", ephemeral=True)
    try:
        await user.kick(reason=f"{i.user}: {reason}")
        await i.response.send_message(f"👢 {user} кикнут. Причина: {reason}")
    except discord.HTTPException as e:
        await i.response.send_message(f"Не удалось: {e}", ephemeral=True)


@bot.tree.command(name="timeout", description="Выдать мут (таймаут)")
@app_commands.guild_only()
async def timeout_cmd(i: discord.Interaction, user: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320], reason: str = "—"):
    if not hier_ok(i, user):
        return await i.response.send_message("Роль цели не ниже вашей.", ephemeral=True)
    try:
        await user.timeout(dt.timedelta(minutes=minutes), reason=f"{i.user}: {reason}")
        await i.response.send_message(f"🔇 {user.mention} в муте на {minutes} мин.")
    except discord.HTTPException as e:
        await i.response.send_message(f"Не удалось: {e}", ephemeral=True)


@bot.tree.command(name="clear", description="Удалить сообщения")
@app_commands.guild_only()
async def clear_cmd(i: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await i.response.defer(ephemeral=True)
    d = await i.channel.purge(limit=amount)
    await i.followup.send(f"🧹 Удалено: {len(d)}")


# ───────────────────────── Развлечения ─────────────────────────
def spouse(gid, uid):
    r = bot.db.execute("select a,b from marriages where guild_id=? and (a=? or b=?)", (gid, uid, uid)).fetchone()
    return None if not r else (r[1] if r[0] == uid else r[0])


def fun_on(i):
    return bot.cfg(i.guild.id)["fun"]["enabled"]


class MarryView(discord.ui.View):
    def __init__(self, a, b):
        super().__init__(timeout=120)
        self.a, self.b = a, b

    async def interaction_check(self, i):
        if i.user.id != self.b.id:
            await i.response.send_message("Это предложение адресовано не вам.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Согласиться", emoji="💍", style=discord.ButtonStyle.success)
    async def yes(self, i, b):
        g = i.guild.id
        if spouse(g, self.a.id) or spouse(g, self.b.id):
            return await i.response.edit_message(content="Кто-то из вас уже в браке.", embed=None, view=None)
        bot.db.execute("insert into marriages values(?,?,?,?)", (g, self.a.id, self.b.id, time.time()))
        bot.db.commit()
        await i.response.edit_message(content=f"💒 {self.a.mention} и {self.b.mention} теперь в браке! Совет да любовь!",
                                      view=None)

    @discord.ui.button(label="Отказаться", emoji="💔", style=discord.ButtonStyle.danger)
    async def no(self, i, b):
        await i.response.edit_message(content=f"💔 {self.b.mention} отказал(а) {self.a.mention}.", view=None)


@bot.tree.command(name="marry", description="💍 Предложить брак")
@app_commands.guild_only()
async def marry(i: discord.Interaction, user: discord.Member):
    if not fun_on(i):
        return await i.response.send_message("Развлечения отключены.", ephemeral=True)
    if user.bot or user.id == i.user.id:
        return await i.response.send_message("Так нельзя 🙂", ephemeral=True)
    if spouse(i.guild.id, i.user.id) or spouse(i.guild.id, user.id):
        return await i.response.send_message("Кто-то из вас уже в браке.", ephemeral=True)
    await i.response.send_message(f"{user.mention}, {i.user.mention} делает вам предложение! 💍",
                                  view=MarryView(i.user, user))


@bot.tree.command(name="divorce", description="💔 Развестись")
@app_commands.guild_only()
async def divorce(i: discord.Interaction):
    s = spouse(i.guild.id, i.user.id)
    if not s:
        return await i.response.send_message("Вы не в браке.", ephemeral=True)
    bot.db.execute("delete from marriages where guild_id=? and (a=? or b=?)", (i.guild.id, i.user.id, i.user.id))
    bot.db.commit()
    await i.response.send_message(f"💔 {i.user.mention} развёлся(лась) с <@{s}>.")


@bot.tree.command(name="marriage", description="💞 Узнать семейное положение")
@app_commands.guild_only()
async def marriage(i: discord.Interaction, user: discord.Member = None):
    user = user or i.user
    r = bot.db.execute("select a,b,ts from marriages where guild_id=? and (a=? or b=?)",
                       (i.guild.id, user.id, user.id)).fetchone()
    if not r:
        return await i.response.send_message(f"{user.display_name} не в браке.")
    other = r[1] if r[0] == user.id else r[0]
    await i.response.send_message(f"💞 {user.mention} в браке с <@{other}> с <t:{int(r[2])}:D>")


ACTIONS = {"hug": ("🤗", "обнимает"), "kiss": ("😘", "целует"), "pat": ("🫶", "гладит по голове"),
           "slap": ("👋", "даёт пощёчину")}
for _n, (_emo, _verb) in ACTIONS.items():
    def _mk(n, emo, verb):
        @bot.tree.command(name=n, description=f"{emo} {verb.capitalize()} участника")
        @app_commands.guild_only()
        async def _cmd(i: discord.Interaction, user: discord.Member):
            if not fun_on(i):
                return await i.response.send_message("Развлечения отключены.", ephemeral=True)
            await i.response.send_message(f"{emo} {i.user.mention} {verb} {user.mention}")
    _mk(_n, _emo, _verb)


# ───────────────────────── Запуск ─────────────────────────
@bot.event
async def on_ready():
    print(f"Вошёл как {bot.user} • серверов: {len(bot.guilds)}")
    if bot.started:
        return
    bot.started = True
    for g in bot.guilds:
        if not bot.has_cfg(g.id):
            await setup_guild(g)
    for t in (auto_backup, afk_check, panel_guard):
        t.start()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Задайте переменную окружения DISCORD_TOKEN")
    bot.run(TOKEN)
