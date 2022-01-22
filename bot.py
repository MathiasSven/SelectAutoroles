from __future__ import annotations

import atexit
import json
import logging
import os
import re
from typing import Any, Final, cast

import attrs
import discord
from discord.ext import commands  # type: ignore[attr-defined]
from dotenv import load_dotenv

load_dotenv()

TOKEN: Final[str] = cast(str, os.getenv("TOKEN"))
GUILD_ID: Final[int] = int(cast(str, os.getenv("GUILD_ID")))


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(levelname)s: %(message)s")

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(stream_handler)


with open("emoji_map.json") as f:
    DEFAULT_EMOJI: Final[dict[str, str]] = json.load(f)


def manage_roles_check(ctx: discord.ApplicationContext) -> bool:
    return cast(bool, commands.has_guild_permissions(manage_roles=True).predicate(ctx))


def is_defualt_emoji(string: str) -> bool:
    return string in DEFAULT_EMOJI.values()


@attrs.frozen
class Autorole:
    role: discord.Role
    emoji: discord.Emoji | str
    description: str
    private: bool

    @classmethod
    def from_dict(cls, guild: discord.Guild, **kwargs) -> Autorole | None:
        role = guild.get_role(kwargs.pop("role"))
        if isinstance((e := kwargs.pop("emoji")), int):
            emoji = discord.utils.get(guild.emojis, id=e)
        else:
            emoji = e
        return cls(role, emoji, **kwargs) if role else None

    def __str__(self) -> str:
        return (
            f"role={self.role.mention}, emoji={self.emoji}, "
            f'private=**{self.private}**, description="{self.description}"'
        )


@attrs.define
class ServerConfig:
    member_role: discord.Role
    color: discord.Color | None
    autoroles: list[Autorole] = attrs.Factory(list)

    def available_autoroles(self, user: discord.Member) -> list[Autorole]:
        if user.top_role >= self.member_role and not self.member_role.is_default():
            return self.autoroles
        return list(filter(lambda x: not x.private, self.autoroles))

    @classmethod
    def from_dict(cls, guild: discord.Guild, **kwargs) -> ServerConfig:
        member_role = guild.get_role(kwargs.pop("member_role")) or guild.default_role
        partial_color = kwargs.pop("color")
        color = discord.Color(partial_color) if partial_color is not None else None
        autoroles = [
            ar_object
            for ar in kwargs.pop("autoroles")
            if (ar_object := Autorole.from_dict(guild, **ar)) is not None
        ]
        return cls(member_role, color, autoroles)

    @classmethod
    def from_guild(cls, guild: discord.Guild) -> ServerConfig:
        return cls(member_role=guild.default_role, color=None)


class Bot(discord.Bot):
    configs: dict[int, ServerConfig] = {}

    def __init__(self):
        super().__init__()
        self.persistent_views_added = False

    async def on_guild_join(self, guild: discord.Guild):
        self.configs[guild.id] = ServerConfig.from_guild(guild)

    async def on_application_command_error(self, context: discord.ApplicationContext, exception):
        if isinstance(exception, discord.errors.ApplicationCommandInvokeError):
            exception = exception.original
        if isinstance(exception, commands.errors.MissingPermissions):
            await context.respond(exception.args[0], ephemeral=True)
        else:
            await super().on_application_command_error(context, exception)


bot = Bot()


class RoleSelect(discord.ui.Select):
    def __init__(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user = interaction.user

        options = [
            discord.SelectOption(
                label=autorole.role.name,
                value=autorole.role.id,
                description=autorole.description,
                emoji=autorole.emoji,
                default=bool(user.get_role(autorole.role.id)),
            )
            for autorole in bot.configs[guild_id].available_autoroles(user)
        ]

        super().__init__(
            placeholder="Select your roles",
            min_values=0,
            max_values=len(options),
            options=sorted(options, key=lambda x: x.label),  # type: ignore[no-any-return]
        )

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        user_roles = set(r.id for r in user.roles)
        available_roles = set(opt.value for opt in self.options)
        selected_roles = set(int(v) for v in self.values)

        to_remove_ids = user_roles & available_roles - selected_roles
        to_add_ids = selected_roles - user_roles

        to_remove = {interaction.guild.get_role(r_id) for r_id in to_remove_ids}
        to_add = {interaction.guild.get_role(r_id) for r_id in to_add_ids}

        await user.add_roles(*to_add, reason="Autorole")
        await user.remove_roles(*to_remove, reason="Autorole")


@bot.slash_command(name="roles", description="Autorole selection menu", guild_ids=[GUILD_ID])
async def roles(ctx: discord.ApplicationContext):
    if bot.configs[ctx.interaction.guild_id].autoroles:
        if bot.configs[ctx.interaction.guild_id].available_autoroles(ctx.interaction.user):
            view = discord.ui.View(RoleSelect(ctx.interaction))
            await ctx.respond("_ _", view=view, ephemeral=True)
        else:
            await ctx.respond("There are no public autoroles available", view=view, ephemeral=True)
    else:
        await ctx.respond("There are no autoroles configured for this server", ephemeral=True)


class SelectRoleButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        style=discord.ButtonStyle.primary,
        label="Select Role(s)",
        custom_id="SelectRoleButtonView:callback",
    )
    async def callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        if bot.configs[interaction.guild_id].autoroles:
            if bot.configs[interaction.guild_id].available_autoroles(interaction.user):
                view = discord.ui.View(RoleSelect(interaction))
                await interaction.response.send_message("_ _", view=view, ephemeral=True)
            else:
                await interaction.response.send_message(
                    "There are no public autoroles available", ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "There are no autoroles configured for this server", ephemeral=True
            )


admin = bot.create_group("admin", "Commands related to mathematics.", [GUILD_ID])


@admin.command(
    name="panel",
    description="Create an autorole embed",
    guild_ids=[GUILD_ID],
    checks=[manage_roles_check],
    options=[
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="title",
            description="The title for the embed",
            required=False,
        ),
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="description",
            description="The description for the embed",
            required=False,
        ),
    ],
)
async def panel(ctx: discord.ApplicationContext, title: str, description: str):
    autoroles = bot.configs[ctx.interaction.guild_id].autoroles
    if not autoroles:
        await ctx.interaction.response.send_message(
            "There are no autoroles registered", ephemeral=True
        )
        return
    embed = discord.Embed(
        title=title,
        colour=bot.configs[ctx.interaction.guild_id].color or discord.Embed.Empty,
        description=description,
    )
    if pub_auto := set(filter(lambda r: not r.private, autoroles)):
        embed.add_field(
            name="Open Roles:",
            value="\u200b\n"
            + "\n".join(f"{ar.emoji} {ar.role.mention}" for ar in pub_auto)
            + "\n\u200b",
        )
    if priv_auto := set(filter(lambda r: r.private, autoroles)):
        embed.add_field(
            name="Member/Invite Roles:",
            value="\u200b\n"
            + "\n".join(f"{ar.emoji} {ar.role.mention}" for ar in priv_auto)
            + "\n\u200b",
            inline=True,
        )

    if guild_icon := ctx.interaction.guild.icon:
        embed.set_footer(icon_url=guild_icon.url, text=ctx.interaction.guild.name)

    await ctx.send(embed=embed, view=SelectRoleButtonView())
    await ctx.respond(
        "Note: If roles are updated, while the button will still "
        "show all roles, the embed will not update automaticly.",
        ephemeral=True,
    )


@admin.command(
    name="add",
    description="Add role to autoroles",
    guild_ids=[GUILD_ID],
    checks=[manage_roles_check],
    options=[
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="emoji",
            description="The emoji related to the role",
            required=True,
        ),
        discord.Option(
            discord.enums.SlashCommandOptionType.role,
            name="role",
            description="The role you would like to add to autoroles",
            required=True,
        ),
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="description",
            description="Description for the role",
            required=False,
            default="",
        ),
        discord.Option(
            discord.enums.SlashCommandOptionType.boolean,
            name="private",
            description="Wheather this role should only be available to members at or above a curtain role",
            required=False,
            default=False,
        ),
    ],
)
async def add(
    ctx: discord.ApplicationContext, emoji: str, role: discord.Role, description: str, private: bool
):
    emoji = emoji.strip()
    guild = ctx.interaction.guild
    if role not in bot.configs[guild.id].autoroles:  # Checks if role is already in autoroles
        if not is_defualt_emoji(emoji):  # Emoji checking
            try:
                concrete_emoji = await commands.EmojiConverter().convert(ctx, emoji)
            except commands.EmojiNotFound:
                await ctx.respond("Invalid emoji", ephemeral=True)
                return
        else:
            concrete_emoji = emoji

        if role < guild.me.top_role:  # Checks if bot has permission to add it
            if len(bot.configs[guild.id].autoroles) == 25:  # Checks if there is space to add it
                await ctx.respond(
                    f"Failed to add {role.mention}. Cannot have more "
                    "then 25 autoroles, remove another one first",
                    ephemeral=True,
                )
                return
            if not role.is_assignable():  # Checks role is not assignable
                await ctx.respond(
                    f"Failed to add {role.mention}. Role is not assignable",
                    ephemeral=True,
                )
                return
            # Passed all checks, add it and inform admin
            bot.configs[guild.id].autoroles.append(
                Autorole(role=role, emoji=concrete_emoji, private=private, description=description)
            )
            if bot.configs[guild.id].member_role is guild.default_role and private:
                await ctx.respond(
                    f"Added {role.mention} to autoroles as private, "
                    "however there is no set member_role in the server, "
                    "set it via `/admin set` so members can autorole this role",
                    ephemeral=True,
                )
            else:
                await ctx.respond(
                    f"Added {role.mention} to autoroles{'as private' if private else ''}",
                    ephemeral=True,
                )
        else:
            await ctx.respond(
                f"Cannot add as {role.mention} is higher then the bot's top role", ephemeral=True
            )
    else:
        await ctx.respond(
            f"Nothing changed as {role.mention} is already in autoroles", ephemeral=True
        )


@admin.command(
    name="remove",
    description="Remove role from autoroles",
    guild_ids=[GUILD_ID],
    checks=[manage_roles_check],
    options=[
        discord.Option(
            discord.enums.SlashCommandOptionType.role,
            name="role",
            description="The role you would like to remove from autoroles",
            required=True,
        ),
    ],
)
async def remove(ctx: discord.ApplicationContext, role: discord.Role):
    autoroles = bot.configs[ctx.interaction.guild_id].autoroles
    try:
        autoroles.remove(next(filter(lambda r: r.role == role, autoroles)))
        await ctx.respond(f"Removed {role.mention} from autoroles", ephemeral=True)
    except (ValueError, StopIteration):
        await ctx.respond(f"Nothing changed as {role.mention} is not in autoroles", ephemeral=True)


@admin.command(
    name="set",
    description="Set options for this server",
    checks=[manage_roles_check],
    options=[
        discord.Option(
            discord.enums.SlashCommandOptionType.role,
            name="member_role",
            description="The role that users need to be able to use private autoroles",
            required=False,
        ),
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="color",
            description='Hex code for the color used for embeds, eg: "0000FF" for blue',
            required=False,
        ),
    ],
)
async def set_config(ctx: discord.ApplicationContext, member_role: discord.Role, color: str):
    server_config = bot.configs[ctx.interaction.guild_id]
    response = []

    if member_role is not None:
        if member_role.is_default():
            response.append(f"Cannot set member_role to {member_role.mention} ❌")
        elif member_role == server_config.member_role:
            response.append(f"Set member_role was already set to {member_role.mention} ❓")
        else:
            server_config.member_role = member_role
            response.append(f"Set member_role to {member_role.mention} ✔️")

    if color is not None:
        pattern = re.compile("[0-9A-F]{6}")
        if not pattern.match(color):
            response.append(f"#{color.upper()} is not a valid color code ❌")
        else:
            if (disc_color := discord.Color(int(color, 16))) == server_config.color:
                response.append(f"Set color was already set to #{color.upper()} ❓")
            else:
                server_config.color = disc_color
                response.append(f"Set color to #{color.upper()} ✔️")

    if not response:
        response.append("Nothing changed as no option was set ❌")

    await ctx.respond("\n".join(response), ephemeral=True)


@admin.command(
    name="view", description="View options set for this server", checks=[manage_roles_check]
)
async def view_config(ctx: discord.ApplicationContext):
    server_config = bot.configs[ctx.interaction.guild_id]
    await ctx.respond(
        f"**member_role:** {server_config.member_role.mention}\n"
        f"**color:** {hex(server_config.color.value) if server_config.color else 'None'}\n\n"
        "**autoroles:** \n" + "\n".join(str(ar) for ar in server_config.autoroles),
        ephemeral=True,
    )


@bot.event
async def on_ready():
    logger.info((t := f"Logged in as {bot.user} (ID: {bot.user.id})"))
    logger.info(len(t) * "-")

    if not bot.persistent_views_added:
        bot.add_view(SelectRoleButtonView())
        bot.persistent_views_added = True

    for guild in bot.guilds:
        bot.configs[guild.id] = ServerConfig.from_guild(guild)

    try:
        with open("db.json", "r") as db:
            db_partial: dict[str, Any] = json.load(db)

            bot.configs = {
                int(guild_id): ServerConfig.from_dict(bot.get_guild(int(guild_id)), **server_config)
                for guild_id, server_config in db_partial.items()
            }

            logger.info("Loaded database into memory")

    except FileNotFoundError:
        logger.info("No database found, skipping db load this time")


def save():
    def serialize(inst: type, field: attrs.Attribute, value: Any) -> Any:  # type: ignore[type-arg]
        if isinstance(value, discord.Role | discord.Emoji):
            return value.id
        elif isinstance(value, discord.Color):
            return value.value
        else:
            return value

    with open("db.json", "w") as db:
        json.dump(
            {
                guild_id: attrs.asdict(server_config, value_serializer=serialize)
                for guild_id, server_config in bot.configs.items()
            },
            db,
        )
        logger.info("Saved to persistent database")


atexit.register(save)

bot.run(TOKEN)
