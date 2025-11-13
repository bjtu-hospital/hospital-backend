import asyncio
import json
import base64
import random
from datetime import datetime

from app.db.base import AsyncSessionLocal
from sqlalchemy import select, and_

from app.models.patient import Patient
from app.models.schedule import Schedule
from app.models.registration_order import RegistrationOrder, OrderStatus


def decode_jwt_sub(token: str):
    try:
        parts = token.split('.')
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # pad
        padding = '=' * (-len(payload_b64) % 4)
        payload_b64 += padding
        decoded = base64.urlsafe_b64decode(payload_b64.encode())
        payload = json.loads(decoded)
        return payload.get('sub')
    except Exception:
        return None


async def main():
    # load generated users
    with open('generated_users.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    async with AsyncSessionLocal() as session:
        # fetch eligible schedules: status=='正常' and remaining_slots>0
        result = await session.execute(select(Schedule).where(and_(Schedule.status == '正常', Schedule.remaining_slots > 0)))
        schedules = result.scalars().all()
        if not schedules:
            print('未找到可用的 schedule（status=正常 且 remaining_slots>0）')
            return

        created = []
        for item in data:
            resp = item.get('response') or {}
            if not resp or resp.get('code') != 0:
                continue
            token = resp.get('message')
            user_id = decode_jwt_sub(token)
            if not user_id:
                print('无法从 token 解出 user_id，跳过', item.get('phonenumber'))
                continue

            # find patient by user_id
            try:
                uid = int(user_id)
            except Exception:
                print('user_id 非整型，跳过', user_id)
                continue

            res = await session.execute(select(Patient).where(Patient.user_id == uid))
            patient = res.scalar_one_or_none()
            if not patient:
                print('未找到 patient，跳过 user_id=', uid)
                continue

            # choose a random schedule
            sched = random.choice(schedules)

            # pick a random status from OrderStatus
            status = random.choice([s.value for s in OrderStatus])

            # build order
            slot_type = getattr(sched, 'slot_type', None)
            if hasattr(slot_type, 'value'):
                slot_type_val = slot_type.value
            else:
                slot_type_val = slot_type

            order = RegistrationOrder(
                patient_id=patient.patient_id,
                user_id=uid,
                doctor_id=sched.doctor_id,
                schedule_id=sched.schedule_id,
                slot_date=sched.date,
                time_section=sched.time_section,
                slot_type=slot_type_val,
                visit_times=None,
                is_waitlist=False,
                waitlist_position=None,
                status=status,
                notes=f"自动生成测试挂号，来源: generated_users.json，手机号={item.get('phonenumber')}",
            )

            session.add(order)

            # 如果状态为会占用名额的类型，则尝试减少 remaining_slots
            if status in (OrderStatus.PENDING.value, OrderStatus.CONFIRMED.value, OrderStatus.COMPLETED.value):
                if sched.remaining_slots and sched.remaining_slots > 0:
                    sched.remaining_slots = sched.remaining_slots - 1
                    # SQLAlchemy will track the change automatically

            created.append({'phonenumber': item.get('phonenumber'), 'user_id': uid, 'patient_id': patient.patient_id, 'schedule_id': sched.schedule_id, 'status': status})

        # commit all
        await session.commit()

        print('已插入挂号订单数量:', len(created))
        for c in created:
            print(c)


if __name__ == '__main__':
    asyncio.run(main())
