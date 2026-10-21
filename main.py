"""
Discord all-in-one бот (discord.py 2.4+)
Приветствия с кнопками, журнал (фото / голосовые / войс), бэкап, анти-краш, анти-ссылки, анти-бот,
браки, заявки, AFK, действия с гифками, /profile, /top, /embed через вебхук, права команд, /settings.
Дизайн: чёрные embed'ы в стиле логов, заголовки на английском (ꜱᴍᴀʟʟ ᴄᴀᴘꜱ).
"""
import asyncio, copy, datetime as dt, io, json, os, random, re, sqlite3, time
from collections import OrderedDict, defaultdict, deque

import discord
from discord import app_commands
from discord.ext import tasks, commands

TOKEN = os.getenv("DISCORD_TOKEN")
UTC = dt.timezone.utc
BLACK = 0x000000  # Чёрный цвет для всех эмбедов
FONT = "smallcaps"  # "smallcaps" | "bold" | "plain" — шрифт заголовков и названий полей
MEDIA_FILE_MAX = 8 * 1024 * 1024    # макс. размер одного фото/ГС для кэша
MEDIA_CACHE_MAX = 64 * 1024 * 1024  # общий размер кэша медиа в памяти
DANGER = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True,
                             manage_channels=True, ban_members=True, kick_members=True,
                             manage_webhooks=True).value

# ── Действия с гифками (добавляйте свои ссылки в списки gifs) ──
ACTIONS = {
    "hug": {"emoji": "🤗", "verb": "обнимает", "desc": "Обнять участника", "back": "Обнять в ответ",
            "gifs": ["https://cdn.otakugifs.xyz/gifs/hug/6d915e537c818fa9.gif",
                     "https://cdn.otakugifs.xyz/gifs/hug/0d8f88e421d8b1eb.gif"]},
    "kiss": {"emoji": "😘", "verb": "целует", "desc": "Поцеловать участника", "back": "Поцеловать в ответ",
             "gifs": ["https://cdn.otakugifs.xyz/gifs/kiss/d440c4eb46056f58.gif",
                      "https://cdn.otakugifs.xyz/gifs/kiss/99c6d80ba787d40a.gif"]},
    "pat": {"emoji": "🫶", "verb": "гладит по голове", "desc": "Погладить участника", "back": "Погладить в ответ",
            "gifs": ["https://cdn.otakugifs.xyz/gifs/pat/13ec930fd42770f6.gif",
                     "https://cdn.otakugifs.xyz/gifs/pat/rzn5K09230.gif"]},
    "slap": {"emoji": "👋", "verb": "даёт пощёчину", "desc": "Дать пощёчину участнику", "back": "Дать сдачи",
             "gifs": ["https://cdn.otakugifs.xyz/gifs/slap/7882244dc2ba254c.gif"]},
}

WELCOME_DEF = {"enabled": False, "channel": None, "embed": True, "plain": "",
               "title": "Welcome",
               "description": "{user.mention}, рады видеть тебя на **{server}**!\nТы {count}-й участник.",
               "color": "#000000", "image": "", "thumbnail": "user", "footer": "Member #{count}",
               "dm": False, "autorole": None, "buttons": [], "wave": True}
GOODBYE_DEF = {**copy.deepcopy(WELCOME_DEF), "title": "Goodbye",
               "description": "**{user.name}** покинул(а) **{server}**.", "footer": "ID: {user.id}",
               "wave": False}

DEFAULTS = {
    "setup_done": False,
    "welcome": WELCOME_DEF,
    "goodbye": GOODBYE_DEF,
    "logs": {"channel": None, "events": {k: True for k in
             ["msg_delete", "msg_edit", "member_join", "member_leave", "roles", "nick",
              "channels", "voice", "bans", "media"]}},
    "backup": {"auto": True, "keep": 5, "last": 0},
    "anticrash": {"enabled": True, "limit": 3, "window": 30, "punish": "ban", "whitelist": []},
    "antilink": {"enabled": False, "mode": "all", "action": "delete", "timeout": 10,
                 "roles": [], "users": [], "channels": [], "domains": []},
    "antibot": {"enabled": True, "whitelist": [], "punish_inviter": False},
    "afk": {"enabled": True, "days": 7, "role": None},
    "apps": {"panel_channel": None, "review_channel": None, "panel_message": None,
             "panel_title": "Applications",
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
                "clear": "manage_messages", "embed": "manage_guild"}

SCHEMA = """
create table if not exists settings(guild_id integer primary key, data text);
create table if not exists activity(guild_id integer, user_id integer, ts real, primary key(guild_id,user_id));
create table if not exists marriages(guild_id integer, a integer, b integer, ts real);
create table if not exists apps(id integer primary key autoincrement, guild_id integer, user_id integer,
    type text, answers text, status text, ts real);
create table if not exists backups(id integer primary key autoincrement, guild_id integer, name text,
    ts real, data text);
create table if not exists msgstats(guild_id integer, user_id integer, day text, n integer,
    primary key(guild_id,user_id,day));
create table if not exists voicestats(guild_id integer, user_id integer, day text, secs real,
    primary key(guild_id,user_id,day));
create table if not exists acts(guild_id integer, a integer, b integer, kind text, n integer,
    primary key(guild_id,a,b,kind));
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


def today():
    return now().date().isoformat()


def since(days):
    return (now().date() - dt.timedelta(days=days - 1)).isoformat()


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


def fdur(s):
    s = int(s or 0)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


# ───────────────────────── Стиль сообщений ─────────────────────────
_SC = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))


def sc(text):
    """Стилизует английские буквы (ꜱᴍᴀʟʟ ᴄᴀᴘꜱ / 𝗯𝗼𝗹𝗱). Кириллица, цифры и эмодзи не меняются."""
    out = []
    for ch in str(text or ""):
        if ch.isascii() and ch.isalpha() and FONT == "smallcaps":
            out.append(_SC[ch.lower()])
        elif ch.isascii() and ch.isalpha() and FONT == "bold":
            out.append(chr((0x1D5D4 + ord(ch) - 65) if ch.isupper() else (0x1D5EE + ord(ch) - 97)))
        else:
            out.append(ch)
    return "".join(out)


def mk(title="", desc=None, user=None, **fields):
    """Единый «лог-стиль»: заголовок, автор с аватаркой, поля, время."""
    e = discord.Embed(title=sc(title) or None, description=desc, color=BLACK, timestamp=now())
    if user is not None:
        e.set_author(name=str(user), icon_url=user.display_avatar.url)
    for k, v in fields.items():
        e.add_field(name=sc(k.replace("_", " ")), value=str(v)[:1024] or "—")
    return e


def fld(e, name, value, inline=True):
    e.add_field(name=sc(name), value=str(value)[:1024] or "—", inline=inline)


def vk(v):
    return {"view": v} if v is not None and v.children else {}


def parse_buttons(text):
    """«Название | https://ссылка» по строкам → [[label, url], ...]"""
    out = []
    for line in str(text).splitlines():
        if "|" not in line:
            continue
        label, url = (x.strip() for x in line.split("|", 1))
        if label and re.match(r"https?://\S+$", url, re.I):
            out.append([label[:80], url])
    return out[:5]


def link_view(btns):
    v = discord.ui.View(timeout=None)
    for label, url in btns:
        v.add_item(discord.ui.Button(label=label, url=url))
    return v if v.children else None


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
        self.media, self.media_bytes = OrderedDict(), 0   # кэш фото/ГС для журнала
        self.pend = defaultdict(int)                      # счётчик сообщений (буфер до записи в БД)
        self.vjoin = {}                                   # активные сессии в войсе
        self.hooks = {}                                   # кэш вебхуков по каналам
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
        self.add_dynamic_items(OpenApp, AppOk, AppNo, WaveBtn)
        await self.tree.sync()

    async def close(self):
        try:
            tick_voice()
            flush_stats()
        except Exception:
            pass
        await super().close()


bot = Bot()


# ───────────────────────── Статистика (сообщения / войс) ─────────────────────────
def flush_stats():
    if not bot.pend:
        return
    rows = [(g, u, d, n) for (g, u, d), n in bot.pend.items()]
    bot.pend.clear()
    bot.db.executemany("insert into msgstats values(?,?,?,?) on conflict(guild_id,user_id,day) "
                       "do update set n=n+excluded.n", rows)
    bot.db.commit()


def add_voice(key, secs):
    if secs < 1:
        return
    bot.db.execute("insert into voicestats values(?,?,?,?) on conflict(guild_id,user_id,day) "
                   "do update set secs=secs+excluded.secs", (key[0], key[1], today(), secs))


def tick_voice():
    t = time.time()
    for key, s in list(bot.vjoin.items()):
        add_voice(key, t - s["c"])
        s["c"] = t
    bot.db.commit()


@tasks.loop(seconds=60)
async def stats_loop():
    flush_stats()
    tick_voice()


def q1(sql, *args):
    r = bot.db.execute(sql, args).fetchone()
    return (r[0] or 0) if r else 0


# ───────────────────────── Логи / аудит ─────────────────────────
async def send_log(g, embed, event=None, files=None):
    c = bot.cfg(g.id)["logs"]
    if event and not c["events"].get(event, True):
        return
    ch = g.get_channel(c["channel"] or 0)
    if ch:
        embed.timestamp = embed.timestamp or now()
        embed.color = BLACK
        try:
            await ch.send(embed=embed, **({"files": files} if files else {}))
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


# ── кэш фото и голосовых сообщений (чтобы показать их в логе после удаления) ──
def media_put(mid, entry):
    bot.media[mid] = entry
    bot.media_bytes += entry["size"]
    while bot.media and (bot.media_bytes > MEDIA_CACHE_MAX or len(bot.media) > 300):
        _, old = bot.media.popitem(last=False)
        bot.media_bytes -= old["size"]


def media_pop(mid):
    e = bot.media.pop(mid, None)
    if e:
        bot.media_bytes -= e["size"]
    return e


def is_voice_att(m, a):
    return bool(getattr(m.flags, "voice", False) or getattr(a, "duration", None)
                or a.filename.startswith("voice-message"))


def media_files(e, media, limit):
    out, used, lines, has_img = [], 0, [], False
    for n, (name, data, voice, dur) in enumerate(media["files"], 1):
        if used + len(data) > limit * 0.95:
            continue
        used += len(data)
        ext = os.path.splitext(name)[1][:6] or (".ogg" if voice else ".bin")
        fname = f"{'voice' if voice else 'file'}_{n}{ext}"
        out.append(discord.File(io.BytesIO(data), filename=fname))
        if voice:
            lines.append(f"🎙 {fname}" + (f" ({fdur(dur)})" if dur else ""))
        else:
            lines.append(f"🖼 {fname}")
            if not has_img and ext.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                e.set_image(url=f"attachment://{fname}")
                has_img = True
    if lines:
        fld(e, "Media", "\n".join(lines), False)
    return out


@bot.listen("on_message")
async def _cache_media(m: discord.Message):
    if not m.guild or m.author.bot or not m.attachments:
        return
    lc = bot.cfg(m.guild.id)["logs"]
    if not lc["channel"] or not lc["events"].get("media", True):
        return
    files, size = [], 0
    for a in m.attachments[:10]:
        ct = (a.content_type or "").lower()
        voice = is_voice_att(m, a)
        if not (voice or ct.startswith("image/") or ct.startswith("audio/")) or a.size > MEDIA_FILE_MAX:
            continue
        try:
            data = await a.read()
        except discord.HTTPException:
            continue
        files.append((a.filename, data, voice, getattr(a, "duration", None)))
        size += len(data)
    if files:
        media_put(m.id, {"uid": m.author.id, "cid": m.channel.id, "files": files, "size": size})


@bot.listen("on_raw_message_delete")
async def _log_del(p):
    if not p.guild_id:
        return
    g = bot.get_guild(p.guild_id)
    if not g:
        return
    m = p.cached_message
    media = media_pop(p.message_id)
    if m is None and media is None:
        return
    if m is not None and m.author.bot:
        return
    uid = m.author.id if m else media["uid"]
    author = m.author if m else g.get_member(uid)
    ch = g.get_channel(p.channel_id)
    e = mk("Message Deleted", user=author, desc=((m.content if m else "") or "*no text*")[:4000])
    fld(e, "Author", f"<@{uid}> (`{uid}`)")
    fld(e, "Channel", ch.mention if ch else f"<#{p.channel_id}>")
    ent = await audit(g, discord.AuditLogAction.message_delete, uid)
    if ent and ent.user and ent.user.id != uid:
        fld(e, "Deleted by", ent.user.mention)
    files = []
    if media and bot.cfg(g.id)["logs"]["events"].get("media", True):
        files = media_files(e, media, g.filesize_limit)
    elif m and m.attachments:
        fld(e, "Attachments", "\n".join(a.filename for a in m.attachments)[:1000], False)
        img = next((a for a in m.attachments if (a.content_type or "").startswith("image/")), None)
        if img:
            e.set_image(url=img.proxy_url)
    await send_log(g, e, "msg_delete", files)


@bot.listen("on_raw_bulk_message_delete")
async def _log_bulk(p):
    for mid in p.message_ids:
        media_pop(mid)
    g = bot.get_guild(p.guild_id) if p.guild_id else None
    if not g:
        return
    ch = g.get_channel(p.channel_id)
    await send_log(g, mk("Messages Purged", Channel=ch.mention if ch else p.channel_id,
                         Count=len(p.message_ids)), "msg_delete")


@bot.listen("on_message_edit")
async def _log_edit(a: discord.Message, b: discord.Message):
    if not a.guild or a.author.bot or a.content == b.content:
        return
    e = mk("Message Edited", user=a.author, Author=f"{a.author.mention} (`{a.author.id}`)",
           Channel=a.channel.mention)
    e.url = b.jump_url
    fld(e, "Before", (a.content or "—")[:1024], False)
    fld(e, "After", (b.content or "—")[:1024], False)
    await send_log(a.guild, e, "msg_edit")


@bot.listen("on_member_update")
async def _log_member(a: discord.Member, b: discord.Member):
    g = b.guild
    if a.roles != b.roles:
        add = [r for r in b.roles if r not in a.roles]
        rem = [r for r in a.roles if r not in b.roles]
        e = mk("Roles Updated", user=b, Member=b.mention)
        if add:
            fld(e, "Added", " ".join(r.mention for r in add))
        if rem:
            fld(e, "Removed", " ".join(r.mention for r in rem))
        ent = await audit(g, discord.AuditLogAction.member_role_update, b.id)
        if ent and ent.user:
            fld(e, "By", ent.user.mention)
        await send_log(g, e, "roles")
    if a.nick != b.nick:
        await send_log(g, mk("Nickname Changed", user=b, Member=b.mention, Before=a.nick or "—",
                             After=b.nick or "—"), "nick")


@bot.listen("on_voice_state_update")
async def _log_voice(m, a, b):
    if m.bot:
        return
    key = (m.guild.id, m.id)
    if a.channel != b.channel:
        bot.written.pop(key, None)
        touch(*key)
        dur = None
        if a.channel is None:
            bot.vjoin[key] = {"s": time.time(), "c": time.time()}
        elif b.channel is None:
            s = bot.vjoin.pop(key, None)
            if s:
                dur = time.time() - s["s"]
                add_voice(key, time.time() - s["c"])
                bot.db.commit()
        elif key not in bot.vjoin:
            bot.vjoin[key] = {"s": time.time(), "c": time.time()}
        if a.channel is None:
            e = mk("Voice Join", user=m, desc=f"{m.mention} joined {b.channel.mention}")
        elif b.channel is None:
            e = mk("Voice Leave", user=m, desc=f"{m.mention} left {a.channel.mention}",
                   Duration=fdur(dur) if dur else "—")
        else:
            e = mk("Voice Move", user=m, desc=f"{m.mention} moved {a.channel.mention} → {b.channel.mention}")
    else:
        if b.channel is None:
            return
        ch = []
        if a.self_stream != b.self_stream:
            ch.append("started streaming" if b.self_stream else "stopped streaming")
        if a.self_video != b.self_video:
            ch.append("turned camera on" if b.self_video else "turned camera off")
        if a.mute != b.mute:
            ch.append("was server muted" if b.mute else "was server unmuted")
        if a.deaf != b.deaf:
            ch.append("was server deafened" if b.deaf else "was server undeafened")
        if not ch:
            return
        e = mk("Voice Update", user=m, desc=f"{m.mention} " + ", ".join(ch) + f" in {b.channel.mention}")
    await send_log(m.guild, e, "voice")


@bot.listen("on_guild_channel_create")
async def _ch_create(ch):
    await send_log(ch.guild, mk("Channel Created", desc=f"{ch.mention} (`{ch.name}`)"), "channels")


@bot.listen("on_guild_channel_delete")
async def _ch_delete(ch):
    bot.snap[(ch.guild.id, ch.id)] = ser_channel(ch)
    await send_log(ch.guild, mk("Channel Deleted", desc=f"`{ch.name}`"), "channels")


@bot.listen("on_guild_role_create")
async def _r_create(r):
    await send_log(r.guild, mk("Role Created", desc=r.mention), "channels")


@bot.listen("on_guild_role_delete")
async def _r_delete(r):
    bot.snap[(r.guild.id, r.id)] = ser_role(r)
    await send_log(r.guild, mk("Role Deleted", desc=f"`{r.name}`"), "channels")


@bot.listen("on_member_ban")
async def _ban(g, u):
    await send_log(g, mk("Member Banned", user=u, desc=f"{u} (`{u.id}`)"), "bans")


@bot.listen("on_member_unban")
async def _unban(g, u):
    await send_log(g, mk("Member Unbanned", user=u, desc=f"{u} (`{u.id}`)"), "bans")


# ───────────────────────── Действия с гифками ─────────────────────────
def spouse(gid, uid):
    r = bot.db.execute("select a,b from marriages where guild_id=? and (a=? or b=?)", (gid, uid, uid)).fetchone()
    return None if not r else (r[1] if r[0] == uid else r[0])


def fun_on(i):
    return bot.cfg(i.guild.id)["fun"]["enabled"]


def action_embed(g, kind, actor, target):
    a = ACTIONS[kind]
    bot.db.execute("insert into acts(guild_id,a,b,kind,n) values(?,?,?,?,1) "
                   "on conflict(guild_id,a,b,kind) do update set n=n+1", (g.id, actor.id, target.id, kind))
    bot.db.commit()
    n = q1("select n from acts where guild_id=? and a=? and b=? and kind=?", g.id, actor.id, target.id, kind)
    e = mk(kind, user=actor,
           desc=f"{a['emoji']} **{actor.display_name}** {a['verb']} **{target.display_name}**")
    e.set_image(url=random.choice(a["gifs"]))
    e.set_footer(text=f"{kind} #{n} • {actor.display_name} → {target.display_name}")
    return e


class BackView(discord.ui.View):
    """Кнопка «ответить тем же» — нажать может только адресат."""
    def __init__(self, kind, actor, target):
        super().__init__(timeout=300)
        self.kind, self.actor, self.target = kind, actor, target
        self.back.label = ACTIONS[kind]["back"]
        self.back.emoji = ACTIONS[kind]["emoji"]

    async def interaction_check(self, i):
        if i.user.id != self.target.id:
            await i.response.send_message("Эта кнопка предназначена не для вас.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ответить", style=discord.ButtonStyle.secondary)
    async def back(self, i: discord.Interaction, b):
        self.stop()
        await i.response.edit_message(view=None)
        await i.followup.send(content=self.actor.mention,
                              embed=action_embed(i.guild, self.kind, self.target, self.actor))


def _make_action(kind):
    a = ACTIONS[kind]

    @bot.tree.command(name=kind, description=f"{a['emoji']} {a['desc']}")
    @app_commands.guild_only()
    async def _cmd(i: discord.Interaction, user: discord.Member):
        if not fun_on(i):
            return await i.response.send_message("Развлечения отключены.", ephemeral=True)
        if user.bot or user.id == i.user.id:
            return await i.response.send_message("Так нельзя 🙂", ephemeral=True)
        await i.response.send_message(content=user.mention, embed=action_embed(i.guild, kind, i.user, user),
                                      view=BackView(kind, i.user, user))


for _k in ACTIONS:
    _make_action(_k)


# ───────────────────────── Приветствия ─────────────────────────
def fmt(text, m):
    return (text.replace("{user.mention}", m.mention).replace("{user.name}", m.display_name)
            .replace("{user.id}", str(m.id)).replace("{user}", str(m)).replace("{server}", m.guild.name)
            .replace("{count}", str(m.guild.member_count)))


def build_msg(c, m):
    content = fmt(c["plain"], m) or None
    if not c["embed"]:
        return content or fmt(c["description"], m), None
    e = discord.Embed(title=sc(fmt(c["title"], m))[:256] or None, color=BLACK,
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


class WaveBtn(discord.ui.DynamicItem[discord.ui.Button], template=r"wave:(?P<u>\d+)"):
    """Кнопка в приветствии: любой участник может поприветствовать новичка (объятие с гифкой)."""
    def __init__(self, uid):
        super().__init__(discord.ui.Button(label="Поприветствовать", style=discord.ButtonStyle.secondary,
                                           custom_id=f"wave:{uid}", emoji="👋"))
        self.uid = uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["u"]))

    async def callback(self, i: discord.Interaction):
        target = i.guild.get_member(self.uid)
        if not target:
            return await i.response.send_message("Участника уже нет на сервере.", ephemeral=True)
        if i.user.id == self.uid:
            return await i.response.send_message("Это приветствие для вас 🙂", ephemeral=True)
        if not fun_on(i):
            return await i.response.send_message("Развлечения отключены.", ephemeral=True)
        await i.response.send_message(content=target.mention, embed=action_embed(i.guild, "hug", i.user, target),
                                      view=BackView("hug", i.user, target))


def welcome_view(c, m, wave=True):
    v = discord.ui.View(timeout=None)
    if wave and c.get("wave"):
        v.add_item(WaveBtn(m.id))
    for label, url in c.get("buttons", []):
        v.add_item(discord.ui.Button(label=label[:80], url=url))
    return v


@bot.listen("on_member_join")
async def _join(m: discord.Member):
    g, c = m.guild, bot.cfg(m.guild.id)
    if m.bot:
        return await handle_bot_join(m)
    await send_log(g, mk("Member Joined", user=m, desc=f"{m.mention} joined the server", ID=m.id,
                         Account=f"<t:{int(m.created_at.timestamp())}:R>", Members=g.member_count), "member_join")
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
        if ch:
            try:
                await ch.send(content=content, embed=e, **vk(welcome_view(w, m)))
            except discord.HTTPException:
                pass
        if w["dm"]:
            try:
                await m.send(content=content, embed=e, **vk(welcome_view(w, m, wave=False)))
            except discord.HTTPException:
                pass


@bot.listen("on_member_remove")
async def _leave(m: discord.Member):
    g, c = m.guild, bot.cfg(m.guild.id)
    roles = " ".join(r.mention for r in m.roles if not r.is_default()) or "—"
    await send_log(g, mk("Member Left", user=m, desc=f"{m} left the server", ID=m.id,
                         Joined=f"<t:{int(m.joined_at.timestamp())}:R>" if m.joined_at else "—",
                         Roles=roles), "member_leave")
    w = c["goodbye"]
    ch = g.get_channel(w["channel"] or 0)
    if w["enabled"] and ch and not m.bot:
        content, e = build_msg(w, m)
        try:
            await ch.send(content=content, embed=e, **vk(welcome_view(w, m, wave=False)))
        except discord.HTTPException:
            pass


# ───────────────────────── Embed через вебхук ─────────────────────────
async def get_hook(ch):
    h = bot.hooks.get(ch.id)
    if h:
        return h
    for w in await ch.webhooks():
        if w.user and w.user.id == bot.user.id and w.token:
            bot.hooks[ch.id] = w
            return w
    w = await ch.create_webhook(name="Embeds", reason="Embed от имени вебхука")
    bot.hooks[ch.id] = w
    return w


async def hook_send(ch, **kw):
    for _ in range(2):
        try:
            return await (await get_hook(ch)).send(wait=True, **kw)
        except discord.NotFound:
            bot.hooks.pop(ch.id, None)  # вебхук удалили — создадим заново
    return None


class EmbedModal(discord.ui.Modal, title="Embed"):
    t = discord.ui.TextInput(label="Заголовок", required=False, max_length=256)
    d = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, max_length=4000)
    img = discord.ui.TextInput(label="Картинка (ссылка)", required=False, max_length=500)
    foot = discord.ui.TextInput(label="Футер", required=False, max_length=200)
    btns = discord.ui.TextInput(label="Название | ссылка (каждая с новой строки)", required=False,
                                style=discord.TextStyle.paragraph, max_length=800,
                                placeholder="Правила | https://example.com\nСайт | https://example.com")

    def __init__(self, ch, name, avatar):
        super().__init__()
        self.ch, self.name, self.avatar = ch, name, avatar

    async def on_submit(self, i: discord.Interaction):
        e = discord.Embed(title=sc(self.t.value) or None, description=self.d.value, color=BLACK)
        if self.img.value.strip().lower().startswith("http"):
            e.set_image(url=self.img.value.strip())
        if self.foot.value.strip():
            e.set_footer(text=self.foot.value.strip())
        kw = {"embed": e, "username": (self.name or i.guild.name)[:80]}
        av = self.avatar or (i.guild.icon.url if i.guild.icon else None)
        if av:
            kw["avatar_url"] = av
        v = link_view(parse_buttons(self.btns.value))
        if v:
            kw["view"] = v
        await i.response.defer(ephemeral=True)
        try:
            msg = await hook_send(self.ch, **kw)
        except discord.Forbidden:
            return await i.followup.send("⛔ У бота нет права «Управление вебхуками» в этом канале.", ephemeral=True)
        except discord.HTTPException as ex:
            return await i.followup.send(f"Не удалось отправить: {ex}", ephemeral=True)
        await i.followup.send(f"✅ Отправлено: {msg.jump_url}" if msg else "Не удалось создать вебхук.",
                              ephemeral=True)


@bot.tree.command(name="embed", description="Отправить embed от имени вебхука (с кнопками-ссылками)")
@app_commands.guild_only()
@app_commands.describe(channel="Канал (по умолчанию текущий)", name="Имя отправителя", avatar="Ссылка на аватарку")
async def embed_cmd(i: discord.Interaction, channel: discord.TextChannel = None, name: str = None,
                    avatar: str = None):
    ch = channel or i.channel
    if not isinstance(ch, discord.TextChannel):
        return await i.response.send_message("Выберите текстовый канал.", ephemeral=True)
    if not ch.permissions_for(i.user).send_messages:
        return await i.response.send_message("⛔ Вы не можете писать в этом канале.", ephemeral=True)
    await i.response.send_modal(EmbedModal(ch, name, avatar))


# ───────────────────────── Профиль и топ ─────────────────────────
@bot.tree.command(name="profile", description="Профиль участника: уровень, сообщения, войс, ранг")
@app_commands.guild_only()
async def profile(i: discord.Interaction, user: discord.Member = None):
    user = user or i.user
    g = i.guild
    flush_stats()
    base = "select coalesce(sum({c}),0) from {t} where guild_id=? and user_id=?"
    ms, vs = base.format(c="n", t="msgstats"), base.format(c="secs", t="voicestats")
    msgs = q1(ms, g.id, user.id)
    m_day = q1(ms + " and day>=?", g.id, user.id, since(1))
    m_week = q1(ms + " and day>=?", g.id, user.id, since(7))
    voice = q1(vs, g.id, user.id)
    v_week = q1(vs + " and day>=?", g.id, user.id, since(7))
    live = bot.vjoin.get((g.id, user.id))
    if live:
        voice += time.time() - live["c"]
        v_week += time.time() - live["c"]
    xp = int(msgs * 10 + (voice // 60) * 5)
    lvl = int((xp / 100) ** 0.5)
    lo, hi = 100 * lvl * lvl, 100 * (lvl + 1) ** 2
    pct = int((xp - lo) / (hi - lo) * 100)
    bar = "▰" * (pct // 10) + "▱" * (10 - pct // 10)
    r_msg = 1 + q1("select count(*) from (select 1 from msgstats where guild_id=? group by user_id "
                   "having sum(n) > ?)", g.id, msgs) if msgs else None
    r_voice = 1 + q1("select count(*) from (select 1 from voicestats where guild_id=? group by user_id "
                     "having sum(secs) > ?)", g.id, voice) if voice >= 1 else None
    e = mk("Profile", user=user, desc=f"**Level {lvl}** • `{xp - lo}/{hi - lo}` XP\n{bar} {pct}%")
    e.set_thumbnail(url=user.display_avatar.url)
    fld(e, "Messages", f"Total: **{msgs:,}**\nToday: {m_day:,}\n7 days: {m_week:,}")
    fld(e, "Voice", f"Total: **{fdur(voice)}**\n7 days: {fdur(v_week)}")
    fld(e, "Rank", f"💬 {'#' + str(r_msg) if r_msg else '—'}\n🎙 {'#' + str(r_voice) if r_voice else '—'}")
    fld(e, "Joined", f"<t:{int(user.joined_at.timestamp())}:D>" if user.joined_at else "—")
    fld(e, "Registered", f"<t:{int(user.created_at.timestamp())}:D>")
    fld(e, "Top Role", user.top_role.mention if user.top_role != g.default_role else "—")
    s = spouse(g.id, user.id)
    if s:
        ts = q1("select ts from marriages where guild_id=? and (a=? or b=?)", g.id, user.id, user.id)
        fld(e, "Married", f"<@{s}> • <t:{int(ts)}:R>")
    else:
        fld(e, "Married", "—")
    got = dict(bot.db.execute("select kind, sum(n) from acts where guild_id=? and b=? group by kind",
                              (g.id, user.id)).fetchall())
    fld(e, "Received", " • ".join(f"{a['emoji']} {got.get(k, 0)}" for k, a in ACTIONS.items()))
    await i.response.send_message(embed=e)


PERIODS = {"day": "Today", "week": "Last 7 days", "month": "Last 30 days", "all": "All time"}


def top_data(g, kind, period):
    flush_stats()
    t, c = ("msgstats", "n") if kind == "msg" else ("voicestats", "secs")
    cond, args = "", [g.id]
    if period != "all":
        cond = " and day>=?"
        args.append(since({"day": 1, "week": 7, "month": 30}[period]))
    rows = bot.db.execute(f"select user_id, sum({c}) s from {t} where guild_id=?{cond} "
                          f"group by user_id order by s desc", args).fetchall()
    return [(u, s) for u, s in rows if (m := g.get_member(u)) and not m.bot]


class TopSel(discord.ui.Select):
    def __init__(self, attr, ph, opts, cur, row):
        super().__init__(placeholder=ph, row=row, options=[
            discord.SelectOption(label=l, value=v, emoji=em, default=(v == cur)) for v, l, em in opts])
        self.attr = attr

    async def callback(self, i):
        v = self.view
        setattr(v, self.attr, self.values[0])
        v.build()
        await i.response.edit_message(embed=v.embed(), view=v)


class TopView(discord.ui.View):
    def __init__(self, uid, guild, kind="msg", period="all"):
        super().__init__(timeout=300)
        self.uid, self.guild, self.kind, self.period = uid, guild, kind, period
        self.build()

    def build(self):
        self.clear_items()
        self.add_item(TopSel("kind", "Category", [("msg", "Messages", "💬"), ("voice", "Voice", "🎙️")],
                             self.kind, 0))
        self.add_item(TopSel("period", "Period", [("day", "Today", "📅"), ("week", "Last 7 days", "🗓️"),
                                                  ("month", "Last 30 days", "📆"), ("all", "All time", "🏆")],
                             self.period, 1))

    def embed(self):
        rows = top_data(self.guild, self.kind, self.period)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for n, (u, s) in enumerate(rows[:10]):
            pos = medals[n] if n < 3 else f"`{n + 1}.`"
            val = f"`{int(s):,}` msgs" if self.kind == "msg" else f"`{fdur(s)}`"
            lines.append(f"{pos} <@{u}> — {val}")
        label = "Messages" if self.kind == "msg" else "Voice"
        e = mk("Top Members", desc=f"**{sc(label)}** • {sc(PERIODS[self.period])}\n\n" +
               ("\n".join(lines) or "*No data yet*"))
        if self.guild.icon:
            e.set_thumbnail(url=self.guild.icon.url)
        mine = next((n + 1 for n, (u, _) in enumerate(rows) if u == self.uid), None)
        e.set_footer(text=f"Your position: #{mine}" if mine else "You are not in the top yet")
        return e

    async def interaction_check(self, i):
        if i.user.id != self.uid:
            await i.response.send_message("Это чужой топ — откройте свой через /top", ephemeral=True)
            return False
        return True


@bot.tree.command(name="top", description="Топ участников по сообщениям и голосу")
@app_commands.guild_only()
async def top_cmd(i: discord.Interaction):
    v = TopView(i.user.id, i.guild)
    await i.response.send_message(embed=v.embed(), view=v)


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
    await send_log(g, mk("Anti-Crash", Violator=f"<@{user_id}>", Punishment=mode, Reason=reason))


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
    await send_log(g, mk("Anti-Bot", Bot=f"{m} (`{m.id}`)",
                         Added_by=inviter.mention if inviter else "unknown", Action="bot kicked"))
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
    bot.pend[(m.guild.id, m.author.id, today())] += 1
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
    await send_log(m.guild, mk("Anti-Link", user=a, Author=a.mention, Channel=m.channel.mention,
                               Text=m.content[:500], Action=c["action"]))


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
        e = mk(f"Application #{app_id}", user=i.user, desc=f"{i.user.mention} (`{i.user.id}`)",
               Type=self.t["name"])
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
    e.add_field(name=sc("Decision"), value=("✅ Одобрена" if ok else "❌ Отклонена") +
                (f"\nПричина: {reason}" if reason else ""), inline=False)
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
    e = discord.Embed(title=sc(a["panel_title"]), description=a["panel_text"], color=BLACK)
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
            elif kind == "btns":
                cur = "\n".join(f"{l} | {u}" for l, u in cur)
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
                elif kind == "btns":
                    v = parse_buttons(v)
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
    return discord.Embed(title=sc(title), description=desc, color=BLACK)


# ── разделы ──
def e_main(p):
    c = p.c
    e = emb("⚙️ Control Panel", "Выберите раздел в меню ниже.")
    e.add_field(name="Модули", inline=False, value="\n".join([
        f"👋 Приветствие: {onoff(c['welcome']['enabled'])}", f"🚪 Прощание: {onoff(c['goodbye']['enabled'])}",
        f"📜 Журнал: {chan(c['logs']['channel'])}", f"🛡️ Анти-краш: {onoff(c['anticrash']['enabled'])}",
        f"🔗 Анти-ссылки: {onoff(c['antilink']['enabled'])}", f"🤖 Анти-бот: {onoff(c['antibot']['enabled'])}",
        f"💤 AFK: {onoff(c['afk']['enabled'])}", f"🎉 Развлечения: {onoff(c['fun']['enabled'])}"]))
    return e


def sec_welcome(key):
    def e_(p):
        c = p.c[key]
        e = emb("👋 Welcome" if key == "welcome" else "🚪 Goodbye")
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
            e.add_field(name="Кнопка «Поприветствовать»", value=onoff(c["wave"]))
            e.add_field(name="Кнопки-ссылки", inline=False,
                        value="\n".join(f"[{l}]({u})" for l, u in c["buttons"]) or "—")
        e.add_field(name="Плейсхолдеры", inline=False,
                    value="`{user.mention}` `{user.name}` `{user.id}` `{user}` `{server}` `{count}`\n"
                          "Миниатюра: `user` / `server` / `none` / ссылка")
        return e

    def items(p):
        async def test(i, pn):
            content, e = build_msg(pn.c[key], i.user)
            await i.response.send_message(content=content, embed=e, ephemeral=True,
                                          **vk(welcome_view(pn.c[key], i.user)))
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
                  Toggle(p, "ЛС", (key, "dm"), 4),
                  clear_btn("Убрать авто-роль", [((key, "autorole"), None)], 4),
                  ModalBtn("Кнопки-ссылки", "Кнопки приветствия",
                           [((key, "buttons"), "Название | ссылка (каждая с новой строки)", "btns", True)], 4),
                  Toggle(p, "Кнопка «Поприветствовать»", (key, "wave"), 4)]
        return r
    return e_, items


LOG_NAMES = {"msg_delete": "Удаление", "msg_edit": "Правки", "member_join": "Входы", "member_leave": "Выходы",
             "roles": "Роли", "nick": "Ники", "channels": "Каналы/роли", "voice": "Голос", "bans": "Баны",
             "media": "Фото/ГС"}


def e_logs(p):
    c = p.c["logs"]
    e = emb("📜 Logs", f"Канал: {chan(c['channel'])}\n«Фото/ГС» — при удалении сообщения бот заново "
                       "прикладывает фото и голосовые сообщения в журнал.")
    e.add_field(name="События", value="\n".join(f"{onoff(c['events'][k])} — {n}" for k, n in LOG_NAMES.items()))
    return e


def i_logs(p):
    r = [Toggle(p, n, ("logs", "events", k), 1 if n_ < 5 else 2) for n_, (k, n) in enumerate(LOG_NAMES.items())]
    return r + [ChanSelect("Канал журнала", ("logs", "channel"), 3)]


def e_backup(p):
    c = p.c["backup"]
    rows = bot.db.execute("select id,name,ts from backups where guild_id=? order by id desc limit 8",
                          (p.guild.id,)).fetchall()
    e = emb("💾 Backups", "Сохраняет роли и каналы с правами. Восстановление: `/backup restore` "
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
    e = emb("🛡️ Anti-Crash", "Следит за массовым удалением/созданием каналов и ролей, банами, киками, вебхуками. "
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
    e = emb("🔗 Anti-Links")
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
    e = emb("🤖 Anti-Bot", "Любой бот не из белого списка кикается сразу после добавления.")
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
    e = emb("📨 Applications", "Кнопки на панели работают всегда: переживают перезапуск, а если сообщение удалят — "
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
    e = emb("💤 Inactivity", "Если участник не писал, не сидел в войсе и не ставил реакции N дней — "
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
    e = emb("🔐 Permissions", "Выберите команду и роли, которым она разрешена. Если роли не заданы — действуют "
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
    e = emb("🎉 Fun", "Команды: `/marry` `/divorce` `/marriage` `/hug` `/kiss` `/pat` `/slap`\n"
                      "Профиль и топ (всегда включены): `/profile` `/top`")
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


def mod_embed(title, target, mod, reason, **extra):
    e = mk(title, user=target, Member=f"{target.mention} (`{target.id}`)", Moderator=mod.mention, **extra)
    fld(e, "Reason", reason, False)
    e.set_thumbnail(url=target.display_avatar.url)
    return e


@bot.tree.command(name="ban", description="Забанить участника")
@app_commands.guild_only()
async def ban_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    if not hier_ok(i, user):
        return await i.response.send_message("Роль цели не ниже вашей.", ephemeral=True)
    try:
        await user.ban(reason=f"{i.user}: {reason}")
        await i.response.send_message(embed=mod_embed("Member Banned", user, i.user, reason))
    except discord.HTTPException as e:
        await i.response.send_message(f"Не удалось: {e}", ephemeral=True)


@bot.tree.command(name="kick", description="Кикнуть участника")
@app_commands.guild_only()
async def kick_cmd(i: discord.Interaction, user: discord.Member, reason: str = "—"):
    if not hier_ok(i, user):
        return await i.response.send_message("Роль цели не ниже вашей.", ephemeral=True)
    try:
        await user.kick(reason=f"{i.user}: {reason}")
        await i.response.send_message(embed=mod_embed("Member Kicked", user, i.user, reason))
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
        await i.response.send_message(embed=mod_embed("Member Timed Out", user, i.user, reason,
                                                      Duration=f"{minutes} min"))
    except discord.HTTPException as e:
        await i.response.send_message(f"Не удалось: {e}", ephemeral=True)


@bot.tree.command(name="clear", description="Удалить сообщения")
@app_commands.guild_only()
async def clear_cmd(i: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await i.response.defer(ephemeral=True)
    d = await i.channel.purge(limit=amount)
    await i.followup.send(f"🧹 Удалено: {len(d)}")


# ───────────────────────── Браки ─────────────────────────
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
        for vc in g.voice_channels:
            for m in vc.members:
                if not m.bot:
                    bot.vjoin[(g.id, m.id)] = {"s": time.time(), "c": time.time()}
    for t in (auto_backup, afk_check, panel_guard, stats_loop):
        t.start()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Задайте переменную окружения DISCORD_TOKEN")
    bot.run(TOKEN)
