from datetime import datetime, timezone
from aiogram import Router,F
from aiogram.filters import Command
from aiogram.types import Message,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton,WebAppInfo
from bot import finance
from bot.db import Database
from bot.config import Settings
from bot.format import money
from bot.keyboards.menu import BTN_FINANCE
from aiogram.fsm.context import FSMContext

router=Router(name='finance')


@router.message(Command('id'))
async def own_id(message:Message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}')


@router.callback_query(F.data.startswith('demo:') | F.data.startswith('ds:') | F.data.startswith('dc:'))
async def obsolete_demo(callback:CallbackQuery):
    await callback.answer('Имитатор отключён. Откройте корзину и выберите оплату через ЮKassa.',show_alert=True)


@router.message(Command('finance'))
@router.message(F.text == BTN_FINANCE)
async def open_finance(message:Message,settings:Settings,state:FSMContext):
    await state.clear()
    if message.from_user.id not in settings.manager_ids:
        await message.answer(f'Финансовый Mini App доступен менеджеру. Ваш Telegram ID: {message.from_user.id}. Владелец проекта должен указать разрешённый ID в MANAGER_IDS локального .env и перезапустить бота.');return
    if not settings.mini_app_url.startswith('https://'):
        await message.answer('Mini App ещё не опубликован по HTTPS. На компьютере с проектом остановите бота и запустите start-miniapp.cmd: он создаст адрес и запустит бота вместе с кабинетом.');return
    if message.chat.type!='private':
        await message.answer('Откройте /finance в личной переписке с ботом');return
    await message.answer('Финансы «Фолиант»',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Открыть Mini App',web_app=WebAppInfo(url=settings.mini_app_url))]]))


@router.message(Command('summary'))
async def summary(message:Message,db:Database,settings:Settings):
    if message.from_user.id not in settings.manager_ids:
        await message.answer('Сводка доступна только менеджеру');return
    parts=(message.text or '').split()
    today=datetime.now(timezone.utc).date().isoformat()
    try:
        if len(parts) not in {1,3}:
            raise ValueError('Формат: /summary YYYY-MM-DD YYYY-MM-DD')
        d=finance.report(db,parts[1] if len(parts)==3 else today[:7]+'-01',parts[2] if len(parts)==3 else today)
    except ValueError as e:
        await message.answer(str(e));return
    result='не вычислен: историческая себестоимость неизвестна' if d['educational_result'] is None else money(d['educational_result'])
    await message.answer(f'Период: {d["start"]} — {d["end"]}, UTC\nДенежный поток: {money(d["cash_flow"])}\nПоступления − возвраты − расходы\nУчебный результат: {result}\nПоступления − возвраты − себестоимость продаж + себестоимость полных возвратов − расходы')
