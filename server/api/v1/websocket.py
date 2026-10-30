"""WebSocket API 路由"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.api.deps import verify_token
from server.infrastructure.realtime import get_ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])

# 心跳配置
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
CLIENT_TIMEOUT = 90  # 客户端超时时间（秒）


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 连接端点

    优化：
    - 添加心跳检测，确保连接活跃
    - 使用 try-except 包装所有发送操作，避免单次错误导致连接断开
    """
    # 查询参数鉴权（浏览器 WebSocket 无法携带 Authorization 头）
    username, session_id = verify_token(websocket.query_params.get("token"))
    if not username or not session_id:
        await websocket.close(code=4401)
        return

    manager = get_ws_manager()
    client_id = str(uuid.uuid4())[:8]

    await manager.connect(client_id, websocket)

    # 创建心跳任务和超时检测任务
    heartbeat_task = None
    timeout_task = None

    try:
        # 发送连接成功消息
        await websocket.send_json({
            "type": "connected",
            "client_id": client_id,
        })

        # 启动心跳任务（服务端定时发送 ping）
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, client_id))

        # 启动超时检测任务
        last_pong_time = asyncio.get_running_loop().time()
        timeout_task = asyncio.create_task(_timeout_monitor(websocket, client_id, manager, last_pong_time))

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                # 客户端心跳响应
                last_pong_time = asyncio.get_running_loop().time()
                # 更新超时任务
                if timeout_task:
                    timeout_task.cancel()
                    timeout_task = asyncio.create_task(_timeout_monitor(websocket, client_id, manager, last_pong_time))
                # 响应 pong
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception as e:
                    logger.warning(f"[{client_id}] 发送 pong 失败: {e}")

            elif msg_type == "subscribe":
                # 订阅任务进度
                job_ids = data.get("job_ids", [])
                manager.subscribe(client_id, job_ids)
                try:
                    await websocket.send_json({
                        "type": "subscribed",
                        "job_ids": job_ids,
                    })
                except Exception as e:
                    logger.warning(f"[{client_id}] 发送订阅确认失败: {e}")

            elif msg_type == "unsubscribe":
                # 取消订阅
                job_ids = data.get("job_ids", [])
                manager.unsubscribe(client_id, job_ids)

            elif msg_type == "user_action":
                # 用户响应（选择匹配结果等）
                job_id = data.get("job_id")
                action_type = data.get("action_type")
                selection = data.get("selection")
                if job_id:
                    manager.resolve_action(job_id, {
                        "action_type": action_type,
                        "selection": selection,
                    })

    except WebSocketDisconnect:
        logger.info(f"[{client_id}] WebSocket 断开连接")
    except asyncio.CancelledError:
        logger.info(f"[{client_id}] WebSocket 任务被取消")
    except Exception as e:
        logger.error(f"[{client_id}] WebSocket 错误: {e}")
    finally:
        # 清理任务和连接
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if timeout_task:
            timeout_task.cancel()
            try:
                await timeout_task
            except asyncio.CancelledError:
                pass
        manager.disconnect(client_id)


async def _heartbeat_loop(websocket: WebSocket, client_id: str):
    """服务端心跳发送循环

    定期向客户端发送 ping，保持连接活跃
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({"type": "ping", "timestamp": asyncio.get_running_loop().time()})
            except Exception as e:
                logger.warning(f"[{client_id}] 发送心跳失败: {e}")
                break
    except asyncio.CancelledError:
        pass


async def _timeout_monitor(websocket: WebSocket, client_id: str, manager, last_pong_time: float):
    """客户端超时检测

    检测客户端是否在规定时间内响应心跳，
    如果超时则主动断开连接
    """
    try:
        while True:
            await asyncio.sleep(10)  # 每10秒检查一次
            current_time = asyncio.get_running_loop().time()
            if current_time - last_pong_time > CLIENT_TIMEOUT:
                logger.warning(f"[{client_id}] 客户端超时（{CLIENT_TIMEOUT}s 无响应），主动断开")
                try:
                    await websocket.close()
                except Exception:
                    pass
                manager.disconnect(client_id)
                break
    except asyncio.CancelledError:
        pass
