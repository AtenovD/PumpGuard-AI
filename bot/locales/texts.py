from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "choose_lang": "Привет! Выбери язык:",
        "lang_set": "Язык установлен: Русский 🇷🇺",
        "welcome": (
            "Я слежу за новыми токенами на Robinhood Chain (hood.fun) и прогоняю каждый через 4 AI-агента на Grok "
            "(аудитор, нарратив, тайминг, финальный проверяющий) плюс risk-менеджер и книгу репутации "
            "криэйторов.\n\n⚠️ Это исследовательский инструмент. Все сделки — <b>dry-run</b> (симуляция), "
            "реальные деньги никогда не используются."
        ),
        "main_menu_title": "Выбери действие:",
        "btn_stats": "📊 Статистика",
        "btn_positions": "📂 Открытые позиции",
        "btn_connect_grok": "🔐 Подключить аккаунт Grok",
        "oauth_open_link": "Открой ссылку и разреши доступ. Ссылка действует 10 минут:",
        "oauth_unavailable": "OAuth Grok пока не настроен администратором.",
        "oauth_connected": "✅ Аккаунт Grok подключён. Теперь для сигналов доступна кнопка «Ask Grok».",
        "oauth_needed": "Сначала подключи аккаунт Grok через главное меню.",
        "oauth_working": "Запрашиваю свежий подробный разбор в Grok…",
        "oauth_failed": "Не удалось получить разбор Grok. Переподключи аккаунт и попробуй снова.",
        "btn_back_menu": "⬅ В меню",
        "positions_title": "📂 Открытые позиции (dry-run):",
        "positions_empty": "Сейчас нет открытых позиций.",
        "position_item": "• {chain} · {symbol} — вход {entry:.6f}, размер {size:.4f}, score {score:.2f}",
        "stats": (
            "📊 Статистика\n\n"
            "Пользователей: {users_total}\n"
            "Проверено токенов за 24ч: {screened_24h}\n"
            "По сетям за 24ч: {chains_24h}\n"
            "Куплено (dry-run) за 24ч: {bought_24h}\n"
            "Сделок сегодня: {trades_today}\n"
            "PnL за сегодня: {pnl_today:+.4f} ETH\n"
            "Открытых позиций: {open_positions}\n"
            "Заблокированных криэйторов: {blocked_creators}"
        ),
        "admin_panel_title": "🛠 Админ-панель",
        "backtest_none": "нет данных",
        "backtest_stage_item": "{stage}: {count}",
        "backtest_position": "{symbol} ({pnl:+.2f}%)",
        "backtest_report": (
            "📈 Backtest · {period_start} — {period_end} UTC\n\n"
            "Сигналов: {total_signals}\n"
            "Куплено / пропущено / воздержались: {total_bought} / {total_skipped} / {total_abstained}\n"
            "Пропуски по этапам: {skip_by_stage}\n"
            "Воздержались: {abstain_by_reason}\n\n"
            "Win rate: {win_rate:.1f}%\n"
            "Средний PnL: {avg_pnl:+.2f}%\n"
            "Медианный PnL: {median_pnl:+.2f}%\n"
            "Чистый PnL: {net_pnl:+.4f} ETH (после комиссий и price impact)\n"
            "Лучшая позиция: {best}\n"
            "Худшая позиция: {worst}\n"
            "Выходы: {exits}\n"
            "Открыто сейчас: {open_positions}\n\n"
            "Выборка: {sample_verdict}\n"
            "95% ДИ среднего PnL: {ci}"
        ),
        "admin_broadcast_prompt": "Пришли сообщение для рассылки всем пользователям.",
        "broadcast_started": "📣 Рассылка начата ({total} получателей)...",
        "broadcast_done": "📣 Рассылка завершена: доставлено {sent}, ошибок {failed}.",
        "admin_channels_title": "📢 Обязательные каналы подписки",
        "admin_channel_detail": "{flag} Язык: {lang}\nТекущий канал: {channel}",
        "admin_channel_set_prompt": "Пришли username канала (например, @my_channel). Бот должен быть админом в этом канале.",
        "admin_channel_set_done": "✅ Канал для {lang} установлен: {channel}",
        "admin_channel_unset_done": "✅ Обязательная подписка для {lang} отключена.",
        "channels_none": "не задан",
        "subscribe_required": "🔒 Чтобы пользоваться ботом, подпишись на канал {channel}, затем нажми «Я подписался».",
        "subscribe_button": "📢 Открыть канал",
        "subscribe_check_button": "✅ Я подписался",
        "subscribe_still_not": "❌ Пока не вижу подписку. Подпишись и попробуй снова.",
        "subscribe_confirmed": "✅ Подписка подтверждена, теперь бот доступен!",
        "risk_disclaimer": (
            "⚠️ Мемкоины на бондинговой кривой обычно теряют стоимость полностью. Этот бот не размещает "
            "реальные сделки и не является финансовым советом."
        ),
        "setup_checklist_title": "⚙️ Для полноценной работы бота нужно донастроить:",
        "setup_item_grok": (
            "1️⃣ <b>GROK_API_KEY</b> — без него агенты Grok не могут анализировать токены, скрининг всегда "
            "будет отклонять всё.\nПолучить ключ: {link}\nДобавь его в .env (или переменные окружения на "
            "хостинге) и перезапусти бота."
        ),
        "setup_item_alert_chat": (
            "2️⃣ <b>ALERT_CHAT_ID</b> — без него сигналы, прошедшие проверку, никуда не публикуются, ты их "
            "просто не увидишь.\nКак получить:\n"
            "• создай канал в Telegram и добавь этого бота туда админом\n"
            "• перешли любое сообщение из канала боту {id_bot} — он покажет chat_id вида -1001234567890\n"
            "Вставь этот id в ALERT_CHAT_ID и перезапусти бота."
        ),
        "setup_checklist_footer": "Это сообщение перестанет приходить, как только переменные будут заполнены.",
    },
    "en": {
        "choose_lang": "Hi! Choose your language:",
        "lang_set": "Language set: English 🇬🇧",
        "welcome": (
            "I watch new Robinhood Chain (hood.fun) token launches and screen each one through 4 Grok-powered agents "
            "(auditor, narrative, timing, final adversarial checker), plus a risk manager and a creator "
            "reputation book.\n\n⚠️ This is a research tool. All trades are <b>dry-run</b> (simulated) — "
            "real money is never used."
        ),
        "main_menu_title": "Choose an action:",
        "btn_stats": "📊 Stats",
        "btn_positions": "📂 Open positions",
        "btn_connect_grok": "🔐 Connect Grok account",
        "oauth_open_link": "Open this link and approve access. It expires in 10 minutes:",
        "oauth_unavailable": "Grok OAuth has not been configured by the administrator yet.",
        "oauth_connected": "✅ Grok account connected. Signals now include an Ask Grok button.",
        "oauth_needed": "Connect your Grok account from the main menu first.",
        "oauth_working": "Requesting a fresh, detailed Grok analysis…",
        "oauth_failed": "Could not get a Grok analysis. Reconnect your account and try again.",
        "btn_back_menu": "⬅ Menu",
        "positions_title": "📂 Open positions (dry-run):",
        "positions_empty": "No open positions right now.",
        "position_item": "• {chain} · {symbol} — entry {entry:.6f}, size {size:.4f}, score {score:.2f}",
        "stats": (
            "📊 Stats\n\n"
            "Users: {users_total}\n"
            "Tokens screened (24h): {screened_24h}\n"
            "By chain (24h): {chains_24h}\n"
            "Bought (dry-run, 24h): {bought_24h}\n"
            "Trades today: {trades_today}\n"
            "PnL today: {pnl_today:+.4f} ETH\n"
            "Open positions: {open_positions}\n"
            "Blocked creators: {blocked_creators}"
        ),
        "admin_panel_title": "🛠 Admin panel",
        "backtest_none": "no data",
        "backtest_stage_item": "{stage}: {count}",
        "backtest_position": "{symbol} ({pnl:+.2f}%)",
        "backtest_report": (
            "📈 Backtest · {period_start} — {period_end} UTC\n\n"
            "Signals: {total_signals}\n"
            "Bought / skipped / abstained: {total_bought} / {total_skipped} / {total_abstained}\n"
            "Skipped by stage: {skip_by_stage}\n"
            "Abstained: {abstain_by_reason}\n\n"
            "Win rate: {win_rate:.1f}%\n"
            "Average PnL: {avg_pnl:+.2f}%\n"
            "Median PnL: {median_pnl:+.2f}%\n"
            "Net PnL: {net_pnl:+.4f} ETH (after fees and price impact)\n"
            "Best position: {best}\n"
            "Worst position: {worst}\n"
            "Exits: {exits}\n"
            "Open now: {open_positions}\n\n"
            "Sample: {sample_verdict}\n"
            "95% CI of mean PnL: {ci}"
        ),
        "admin_broadcast_prompt": "Send the message you want to broadcast to all users.",
        "broadcast_started": "📣 Broadcast started ({total} recipients)...",
        "broadcast_done": "📣 Broadcast finished: delivered {sent}, failed {failed}.",
        "admin_channels_title": "📢 Mandatory subscription channels",
        "admin_channel_detail": "{flag} Language: {lang}\nCurrent channel: {channel}",
        "admin_channel_set_prompt": "Send the channel username (e.g. @my_channel). The bot must be an admin in that channel.",
        "admin_channel_set_done": "✅ Channel for {lang} set to: {channel}",
        "admin_channel_unset_done": "✅ Mandatory subscription for {lang} disabled.",
        "channels_none": "not set",
        "subscribe_required": "🔒 To use this bot, subscribe to {channel}, then tap \"I've subscribed\".",
        "subscribe_button": "📢 Open channel",
        "subscribe_check_button": "✅ I've subscribed",
        "subscribe_still_not": "❌ Still not seeing your subscription. Subscribe and try again.",
        "subscribe_confirmed": "✅ Subscription confirmed, the bot is now available!",
        "risk_disclaimer": (
            "⚠️ Bonding-curve memecoins usually lose their value entirely. This bot never places real "
            "trades and is not financial advice."
        ),
        "setup_checklist_title": "⚙️ To run at full capacity, the bot still needs:",
        "setup_item_grok": (
            "1️⃣ <b>GROK_API_KEY</b> - without it the Grok agents can't analyze tokens, screening will "
            "reject everything.\nGet a key here: {link}\nAdd it to .env (or your hosting provider's "
            "environment variables) and restart the bot."
        ),
        "setup_item_alert_chat": (
            "2️⃣ <b>ALERT_CHAT_ID</b> - without it, signals that pass screening are never published "
            "anywhere, you simply won't see them.\nHow to get it:\n"
            "• create a Telegram channel and add this bot there as an admin\n"
            "• forward any message from that channel to {id_bot} - it will show a chat_id like -1001234567890\n"
            "Paste that id into ALERT_CHAT_ID and restart the bot."
        ),
        "setup_checklist_footer": "This message will stop appearing once those variables are set.",
    },
}


def t(lang: str, key: str, **kwargs: object) -> str:
    lang = lang if lang in TEXTS else "ru"
    template = TEXTS[lang].get(key, key)
    return template.format(**kwargs) if kwargs else template
