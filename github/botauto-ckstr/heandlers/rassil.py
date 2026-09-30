from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from Database.data import add_user, give_all, get_count
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv
import os
user = Router()

load_dotenv()
admin_id = int(os.getenv('ADMIN_ID'))
class sender(StatesGroup):
    photo = State()
    text = State()

admin_panel = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Рассылка", callback_data="rass")],
        [InlineKeyboardButton(text="Статистика", callback_data="state")],
    ]
)

@user.message(Command("admin"))
async def start_rassil(message: Message):
    user_id1 = message.from_user.id
    if user_id1 == admin_id:
        await message.answer("Выбери действие:", reply_markup=admin_panel)
    else: 
        await message.answer("вход запрещен")

@user.callback_query(F.data == "rass")
async def rass_sender(callback: CallbackQuery, state: FSMContext):
    user_id1 = callback.from_user.id
    if user_id1 == admin_id:
        await state.set_state(sender.photo)
        await callback.message.answer('пришлите фото')
        await callback.answer()
    else: 
        await callback.message.answer("вход запрещен")
        await callback.answer()

@user.callback_query(F.data == "state")
async def state_sender(callback: CallbackQuery, state: FSMContext):
    user_id1 = callback.from_user.id
    if user_id1 == admin_id:
        count = get_count()
        await callback.message.answer('📊 Статистика бота: ' + str(count))
        await callback.answer()
    else:
         await callback.message.answer("вход запрещен")
         await callback.answer()

@user.message(sender.photo, F.photo)
async def photo(message: Message, state: FSMContext):
    user_id1 = message.from_user.id
    if user_id1 == admin_id:
        photo_id = message.photo[-1].file_id
        await state.update_data(photo=photo_id)
        await message.answer('пришлите текст')
        await state.set_state(sender.text)
    else:
        await message.answer("вход запрещен")


@user.message(sender.text)
async def send_all(message: Message, state: FSMContext, bot : Bot):
        user_id1 = message.from_user.id
        if user_id1 == admin_id:        
            text = message.text
            data = await state.get_data()
            photo_user = data.get("photo")
            users = give_all()
            for user_id in users:
                try:
                    await bot.send_photo(user_id, photo_user, caption=text, parse_mode="Markdownv2")
                except Exception as e:
                    message.answer('трабл' + str(e))
            count = get_count()
            await message.answer('📊 Разосланно для: ' + str(count) + "пользователей")
            await state.clear()
        else:
            await message.answer('запрещенно')
            await state.clear()


        
