from aiogram import Router
from . import order_status, creation, lists, observer, actions, archive, common, productivity

router = Router()
router.include_router(order_status.router)
router.include_router(creation.router)
router.include_router(lists.router)
router.include_router(observer.router)
router.include_router(actions.router)
router.include_router(archive.router)
router.include_router(productivity.router)
router.include_router(common.router)
