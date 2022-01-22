import atexit
import json
import logging
import os
from typing import Final, TypedDict, cast

import discord
from discord.ext import commands  # type: ignore[attr-defined]
from dotenv import load_dotenv

load_dotenv()

TOKEN: Final[str] = cast(str, os.getenv("TOKEN"))
GUILD_ID: Final[int] = int(cast(str, os.getenv("GUILD_ID")))

manage_roles = commands.has_permissions(manage_roles=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(levelname)s: %(message)s")

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(stream_handler)


class PartialServerConfigs(TypedDict, total=False):
    rolemojis: dict[str, int | str]
    color: int


class ServerConfigs(TypedDict, total=False):
    autoroles: dict[discord.Role, discord.Emoji | str]
    color: int


class Bot(discord.Bot):
    configs: dict[int, ServerConfigs] = {}

    def __init__(self):
        super().__init__()
        self.persistent_views_added = False
        with open("emoji_map.json") as f:
            self.default_emoji = json.load(f)

    def is_defualt_emoji(self, string: str) -> bool:
        return string in self.default_emoji.values()

    @classmethod
    def autoroles(cls, guild_id: int) -> dict[discord.Role, discord.Emoji | str]:
        return cls.configs[guild_id]["autoroles"]

    @classmethod
    def color(cls, guild_id: int) -> discord.Color | None:
        if color_value := cls.configs[guild_id].get("color", None):
            return discord.Color(color_value)
        else:
            return None

    async def on_guild_join(self, guild: discord.Guild):
        self.configs[guild.id] = ServerConfigs(autoroles=dict())


bot = Bot()


class RoleSelect(discord.ui.Select):
    def __init__(self, interaction: discord.Interaction):
        guild_id: int = interaction.guild_id
        user = interaction.user

        options = [
            discord.SelectOption(
                label=role.name,
                value=role.id,
                description="",
                emoji=emoji,
                default=bool(user.get_role(role.id)),
            )
            for role, emoji in bot.autoroles(guild_id).items()
        ]

        super().__init__(
            placeholder="Select your roles",
            min_values=0,
            max_values=len(options),
            options=options,
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


@bot.slash_command(name="roles", description="Auto role selection menu", guild_ids=[GUILD_ID])
async def roles(ctx: discord.ApplicationContext):
    if bot.autoroles(ctx.interaction.guild_id):
        view = discord.ui.View(RoleSelect(ctx.interaction))
        await ctx.respond("_ _", view=view, ephemeral=True)
    else:
        await ctx.respond("There are no autoroles configured for this server.", ephemeral=True)


class SelectRoleButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        style=discord.ButtonStyle.primary,
        label="Select Role(s)",
        custom_id="SelectRoleButtonView:callback",
    )
    async def callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        if bot.autoroles(interaction.guild_id):
            view = discord.ui.View(RoleSelect(interaction))
            await interaction.response.send_message("_ _", view=view, ephemeral=True)
        else:
            await interaction.response.send_message(
                "There are no autoroles configured for this server.", ephemeral=True
            )


@bot.slash_command(
    name="panel",
    description="Create an autorole embed",
    guild_ids=[GUILD_ID],
    checks=[manage_roles],
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
        discord.Option(
            discord.enums.SlashCommandOptionType.string,
            name="color",
            description='Hex code for the embed color, eg: "0000FF" for blue',
            required=False,
        ),
    ],
)
async def panel(ctx: discord.ApplicationContext, title: str, description: str, color: str):
    autoroles = bot.autoroles(ctx.interaction.guild_id).items()
    if not autoroles:
        await ctx.interaction.response.send_message(
            "There are no autoroles registered", ephemeral=True
        )
        return
    embed = discord.Embed(
        title=title,
        colour=discord.Color(int(color, 16))
        if color
        else None or bot.color(ctx.interaction.guild_id) or discord.Embed.Empty,
        description=description,
    )
    embed.add_field(
        name="Available Roles:",
        value="\u200b\n" + "\n".join((f"{e} {r.mention}" for r, e in autoroles)) + "\n\u200b",
    )
    if guild_icon := ctx.interaction.guild.icon:
        embed.set_footer(icon_url=guild_icon.url, text=ctx.interaction.guild.name)
    await ctx.send(embed=embed, view=SelectRoleButtonView())
    await ctx.respond(
        "Note: If roles are updated, while the button will still "
        "show all roles, the embed will not update automaticly.",
        ephemeral=True,
    )


@bot.slash_command(
    name="add",
    description="Add roles to autoroles",
    guild_ids=[GUILD_ID],
    checks=[manage_roles],
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
    ],
)
async def add(ctx: discord.ApplicationContext, emoji: str, role: discord.Role):
    if role not in bot.autoroles(ctx.interaction.guild_id):
        if not bot.is_defualt_emoji(emoji):
            try:
                concrete_emoji = await commands.EmojiConverter().convert(ctx, emoji)
            except commands.EmojiNotFound:
                await ctx.respond("Invalid emoji", ephemeral=True)
                return
        else:
            concrete_emoji = emoji
        if role < ctx.interaction.guild.me.top_role:
            if len(bot.autoroles(ctx.interaction.guild_id)) == 25:
                await ctx.respond(
                    f"Failed to add {role.mention}. Cannot have more "
                    "then 25 autoroles, remove another one first",
                    ephemeral=True,
                )
                return
            if not role.is_assignable():
                await ctx.respond(
                    f"Failed to add {role.mention}. Role is not assignable",
                    ephemeral=True,
                )
                return
            bot.autoroles(ctx.interaction.guild_id).update({role: concrete_emoji})
            await ctx.respond(f"Added {role.mention} to autoroles", ephemeral=True)
        else:
            await ctx.respond(
                f"Cannot add as {role.mention} is higher then the bot's top role", ephemeral=True
            )
    else:
        await ctx.respond(
            f"Nothing changed as {role.mention} is already in autoroles", ephemeral=True
        )


@bot.slash_command(
    name="remove",
    description="Remove role from autoroles",
    guild_ids=[GUILD_ID],
    checks=[manage_roles],
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
    if bot.autoroles(ctx.interaction.guild_id).pop(role, False):
        await ctx.respond(f"Removed {role.mention} from autoroles", ephemeral=True)
    else:
        await ctx.respond(f"Nothing changed as {role.mention} is not in autoroles", ephemeral=True)


@bot.event
async def on_ready():
    logger.info((t := f"Logged in as {bot.user} (ID: {bot.user.id})"))
    logger.info(len(t) * "-")

    if not bot.persistent_views_added:
        bot.add_view(SelectRoleButtonView())
        bot.persistent_views_added = True

    for guild in bot.guilds:
        bot.configs[guild.id] = ServerConfigs(autoroles=dict())
    try:
        with open("db.json", "r") as db:
            db_partial: dict[int, PartialServerConfigs] = json.load(db)

            for guild_id, server_configs in db_partial.items():
                guild = bot.get_guild(int(guild_id))
                rolemojis_loaded = {}
                for role_id, emoji in server_configs["rolemojis"].items():
                    if isinstance(emoji, int):
                        concrete_emoji = bot.get_emoji(emoji)
                    else:
                        concrete_emoji = emoji
                    rolemojis_loaded[guild.get_role(int(role_id))] = concrete_emoji
                bot.configs[guild.id]["autoroles"] = rolemojis_loaded
            logger.info("Loaded database into memory")
    except FileNotFoundError:
        logger.info("No database found, skipping db load this time")


def save():
    with open("db.json", "w") as db:
        db_partial: dict[int, PartialServerConfigs] = {}
        for guild_id, server_configs in bot.configs.items():
            rolemojis = server_configs["autoroles"]
            db_partial[guild_id] = dict()
            db_partial[guild_id]["rolemojis"] = {
                str(r.id): e if isinstance(e, str) else e.id for r, e in rolemojis.items()
            }
        json.dump(db_partial, db)
        logger.info("Saved to persistent database")


atexit.register(save)

bot.run(TOKEN)
