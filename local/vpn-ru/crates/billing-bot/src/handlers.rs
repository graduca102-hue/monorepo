use std::sync::Arc;

use teloxide::{prelude::*, utils::command::BotCommands};

use crate::BotCtx;

#[derive(BotCommands, Clone)]
#[command(rename_rule = "lowercase", description = "Команды:")]
pub enum Command {
    #[command(description = "начать, получить пробный доступ")]
    Start,
    #[command(description = "моя ссылка-подписка и статус")]
    Status,
    #[command(description = "тарифы и оплата")]
    Buy,
    #[command(description = "как подключиться")]
    Help,
}

pub async fn dispatch(bot: Bot, ctx: Arc<BotCtx>) {
    let handler = Update::filter_message().branch(
        dptree::entry().filter_command::<Command>().endpoint(on_command),
    );

    Dispatcher::builder(bot, handler)
        .dependencies(dptree::deps![ctx])
        .enable_ctrlc_handler()
        .build()
        .dispatch()
        .await;
}

async fn on_command(bot: Bot, ctx: Arc<BotCtx>, msg: Message, cmd: Command) -> ResponseResult<()> {
    let tg_id = msg.from.as_ref().map(|u| u.id.0 as i64).unwrap_or_default();
    let username = msg.from.as_ref().and_then(|u| u.username.clone());

    match cmd {
        Command::Start => {
            let token = ensure_user(&ctx, tg_id, username).await;
            match token {
                Ok(t) => {
                    bot.send_message(
                        msg.chat.id,
                        format!(
                            "Готово. Пробный доступ на 3 дня активирован.\n\n\
                             Ссылка-подписка (вставь в Hiddify / v2rayNG / sing-box):\n{}/sub/{}\n\n\
                             /help — как подключиться\n/buy — продлить",
                            ctx.sub_base, t
                        ),
                    )
                    .await?;
                }
                Err(e) => {
                    tracing::error!(error = %e, "ensure_user");
                    bot.send_message(msg.chat.id, "Временная ошибка, попробуй позже.").await?;
                }
            }
        }
        Command::Status => {
            match user_status(&ctx, tg_id).await {
                Ok(s) => bot.send_message(msg.chat.id, s).await?,
                Err(_) => bot.send_message(msg.chat.id, "Профиль не найден. Нажми /start").await?,
            };
        }
        Command::Buy => {
            bot.send_message(msg.chat.id, tariffs_text()).await?;
            // TODO: send_invoice for Stars; render a TRON address + memo for USDT
        }
        Command::Help => {
            bot.send_message(msg.chat.id, HELP).await?;
        }
    }
    Ok(())
}

async fn ensure_user(ctx: &BotCtx, tg_id: i64, username: Option<String>) -> anyhow::Result<String> {
    let resp: serde_json::Value = ctx
        .http
        .post(format!("{}/users", ctx.api_base))
        .header("authorization", format!("Bearer {}", ctx.api_token))
        .json(&serde_json::json!({ "tg_id": tg_id, "username": username }))
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    Ok(resp["sub_token"].as_str().unwrap_or_default().to_string())
}

async fn user_status(ctx: &BotCtx, tg_id: i64) -> anyhow::Result<String> {
    let u: serde_json::Value = ctx
        .http
        .get(format!("{}/users/{}", ctx.api_base, tg_id))
        .header("authorization", format!("Bearer {}", ctx.api_token))
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    Ok(format!(
        "Статус: {}\nДо: {}\nТрафик: {} / {}\nСсылка: {}/sub/{}",
        u["status"].as_str().unwrap_or("?"),
        u["expires_at"].as_str().unwrap_or("—"),
        u["traffic_used_bytes"].as_i64().unwrap_or(0),
        u["traffic_limit_bytes"].as_i64().map(|b| b.to_string()).unwrap_or_else(|| "∞".into()),
        ctx.sub_base,
        u["sub_token"].as_str().unwrap_or_default(),
    ))
}

fn tariffs_text() -> &'static str {
    "Тарифы:\n\
     • 1 месяц — 150 ⭐ / 2 USDT\n\
     • 3 месяца — 400 ⭐ / 5 USDT\n\
     • 1 год — 1400 ⭐ / 18 USDT\n\n\
     Оплата: Telegram Stars или USDT (TRC-20). Ответь номером тарифа."
}

const HELP: &str = "1. Установи Hiddify (Android/iOS/Windows) или v2rayNG.\n\
     2. Добавь профиль по ссылке-подписке из /status.\n\
     3. Включи. Приложение само выберет рабочий сервер.\n\
     Если перестало работать — просто обнови подписку в приложении, \
     сервер мог смениться.";
