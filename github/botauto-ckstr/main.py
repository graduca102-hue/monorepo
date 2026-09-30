import asyncio
from playwright.async_api import async_playwright
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
import time
from datetime import datetime, timezone, timedelta
import random
from heandlers.rassil import user, send_all
from Database.data import create_db
from heandlers.pars_raspis import str_day
import os
from dotenv import load_dotenv
from Database.data import add_user, give_all
from aiogram.types import FSInputFile

load_dotenv()
TOKEN = os.getenv('TOKEN')

bot = Bot(token=TOKEN)
utc_5 = timezone(timedelta(hours=5))
current_time = datetime.now(utc_5).strftime("%H:%M")
dp = Dispatcher()
dp.include_router(user)



async def zaplan(bot: Bot):
    while True:
        utc_5 = timezone(timedelta(hours=5))
        now = datetime.now(utc_5)
        target_time = now.replace(hour=17, minute=0, second=0, microsecond=0)

        if now > target_time:
            target_time += timedelta(days=1)
        wait_second = (target_time - now).total_seconds()

        await asyncio.sleep(wait_second)
        photo_screen = await str_day()
        photo_user = FSInputFile(photo_screen) 

        text = current_time
        users = give_all()

        for user_id in users:
            try:
                    await bot.send_photo(user_id, photo_user, caption="Сейсас" + str(text) + "а значит РАСПИСАНИЕ!", parse_mode="Markdownv2")
            except Exception as e:
                    print('трабл', e)


#прописывание кнопок
day_raspis = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Актуально на день", callback_data="day")],
        [InlineKeyboardButton(text="Размер?", callback_data="size")],
        [InlineKeyboardButton(text="🤖 Ai-form", callback_data="autotest")],
    ]
)

size_organ = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text='Сиськи 🍒', callback_data="boobs")],
        [InlineKeyboardButton(text='Член 🍆', callback_data="chlen")],
        [InlineKeyboardButton(text='Пизда 🐚', callback_data='pizda')],
        [InlineKeyboardButton(text='Назад', callback_data='nazad1')],
    ]
)

#назначение команд
@dp.message(Command("start"))
async def start_proje(message: Message):
    await message.answer("Привет выбери действие:", reply_markup=day_raspis)
    add_user(message.from_user.id)


@dp.callback_query(F.data == "size")
async def show_size(callback: CallbackQuery):
    await callback.message.edit_text("Выбери что хочешь измерить", reply_markup=size_organ)

    await callback.answer()
  

@dp.message(Command("size"))
async def size(message: Message):
    await message.answer("Выбери что хочешь измерить", reply_markup=size_organ)
    add_user(message.from_user.id)
    await callback.answer()
    
@dp.callback_query(F.data=="boobs")
async def boobs(callback: CallbackQuery):
    boobs = str(random.randint(1, 4))
    await callback.message.answer("У тебя " + str(boobs) + "-ый размер сисек 🍒")
    await callback.answer()

@dp.callback_query(F.data=="chlen")
async def clen(callback: CallbackQuery):
    chlen1 = random.randint(1, 15)
    if chlen1 > 10:
        await callback.message.answer("Ебать что за башня твой член " + str(chlen1) + "cm")
    if chlen1 < 10:
        await callback.message.answer('Ну че то маленький пиздец...' + str(chlen1) + "cm")
    await callback.answer()

@dp.callback_query(F.data=="pizda")
async def pizda(callback: CallbackQuery):
    pizda = random.randint(1, 23)
    await callback.message.answer("у тебя есть пизда? эм.." + str(pizda) + "cm")
    await callback.answer()

@dp.callback_query(F.data=="nazad1")
async def nazad1(callback: CallbackQuery):
    await callback.message.edit_text("Привет выбери действие:", reply_markup=day_raspis)
    await callback.answer()
    

@dp.callback_query(F.data == "day")
async def handle_answer(callback: CallbackQuery):
    if callback.data == "day":
        screenshot_path = await str_day()
        await callback.message.edit_text("Делаю скриншот расписания")
        time.sleep(4)
        await callback.message.edit_text("⏳")
        time.sleep(2)
        from aiogram.types import FSInputFile
        photo = FSInputFile(screenshot_path)
        await callback.message.answer_photo(photo=photo, caption="время в которое был сделан скриншот: " + current_time)
    await callback.answer()


async def main():
    asyncio.create_task(zaplan(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    create_db()
    asyncio.run(main())