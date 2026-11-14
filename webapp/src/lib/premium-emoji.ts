/**
 * Premium emoji catalogue for the webapp.
 * Document ids verified against Beasgohan-code/Videl0 (premium_emojis.py and
 * webapp/src/lib/premium-emoji.ts). Rendered through Telegram only when the bot
 * is eligible; the UI always shows the unicode fallback.
 */
export type PremiumEmoji = { id: string; fallback: string };

export const PREMIUM_EMOJI: Record<string, PremiumEmoji> = {
  orb: { id: "5042302287087666158", fallback: "🔮" },
  diamond: { id: "5427168083074628963", fallback: "💎" },
  line: { id: "5456140674028019486", fallback: "➖" },
  spark: { id: "5325547803936572038", fallback: "✨" },
  hex: { id: "5251203410396458957", fallback: "⬡" },
  arrow: { id: "5416117059207572332", fallback: "➡️" },
  check: { id: "5084979757905347540", fallback: "✅" },
  cross: { id: "5210952531676504517", fallback: "❌" },
  warn: { id: "5447644880824181073", fallback: "⚠️" },
  ban: { id: "5260293700088511294", fallback: "🚫" },
  lock: { id: "5296369303661067030", fallback: "🔒" },
  shield: { id: "6030445631921721471", fallback: "🛡️" },
  crown: { id: "5217822164362739968", fallback: "👑" },
  stats: { id: "5231200819986047254", fallback: "📊" },
  robot: { id: "6030400221232501136", fallback: "🤖" },
  fire: { id: "5413613476334192594", fallback: "🔥" },
  star: { id: "5427201476847465985", fallback: "⭐" },
  clock: { id: "5343863823230952832", fallback: "⏰" },
  calendar: { id: "5413634892084188038", fallback: "📅" },
  link: { id: "5397842136791404896", fallback: "🔗" },
  trash: { id: "5445351100049512183", fallback: "🗑️" },
  user: { id: "5373036007794358183", fallback: "👤" },
  users: { id: "5373328827212212482", fallback: "👥" },
  message: { id: "5443038326535759644", fallback: "💬" },
  bell: { id: "5458666250834138261", fallback: "🔔" },
  search: { id: "5393043212897174356", fallback: "🔍" },
  gear: { id: "5341715473882955310", fallback: "⚙️" },
  plug: { id: "5447410659077661506", fallback: "🔌" },
  rocket: { id: "5433672972221942658", fallback: "🚀" },
  code: { id: "5375136778328049525", fallback: "💻" },
  key: { id: "5444980743319929542", fallback: "🔑" },
  eye: { id: "5373401953171885116", fallback: "👁️" },
  graph: { id: "5372926953978341366", fallback: "📈" },
  server: { id: "5375235598304028890", fallback: "🖥️" },
  gavel: { id: "5425026489688769338", fallback: "⚖️" },
  book: { id: "5348125953090403204", fallback: "📖" },
};

export function emoji(name: string): string {
  return (PREMIUM_EMOJI[name] ?? PREMIUM_EMOJI.diamond).fallback;
}

export function emojiId(name: string): string {
  return (PREMIUM_EMOJI[name] ?? PREMIUM_EMOJI.diamond).id;
}

/** `<tg-emoji>` markup used when previewing bot messages in the webapp. */
export function tgEmoji(name: string): string {
  const item = PREMIUM_EMOJI[name] ?? PREMIUM_EMOJI.diamond;
  return `<tg-emoji emoji-id="${item.id}">${item.fallback}</tg-emoji>`;
}
