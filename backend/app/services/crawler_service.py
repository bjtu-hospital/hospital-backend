import json
import asyncio
import aiohttp
import ssl
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.models.schedule import Schedule, SlotType
from app.models.clinic import Clinic
from app.models.hospital_area import HospitalArea
from app.models.doctor import Doctor
from app.core.exception_handler import BusinessHTTPException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 约定：crawler 集成使用 two JSON sources
# 1) all.json (结构为顶层数组, 每元素含 data 列表与 weekSchedule)
# 2) crawler_data.json (顶层数组，医生基本信息，用于补充医生照片等 - 暂保留扩展)
# 本次实现：仅导入 all.json 生成排班数据 (area/clinic/schedule)，医生按姓名匹配已有记录，不存在则跳过该排班。

# 基础路径：app 目录与 backend 目录
APP_DIR = Path(__file__).resolve().parents[1]  # backend/app
BACKEND_DIR = APP_DIR.parent  # backend

# 统一路径：将 all.json 与 schedule 目录放在 backend 根下；医生列表放在 app/static/json 下
ALL_JSON_PATH = BACKEND_DIR / "all.json"
CRAWLER_DATA_PATH = APP_DIR / "static" / "json" / "crawler_data.json"
SCHEDULE_FOLDER = BACKEND_DIR / "schedule"

# 爬虫常量
SCHEDULE_URL = "https://www.puh3.net.cn/aop_web/industry/patient/static/userDoctor/scheduleOfDoc/"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
}

class CrawlerImportStats:
    def __init__(self):
        self.areas_created = 0
        self.clinics_created = 0
        self.schedules_inserted = 0
        self.schedules_updated = 0
        self.schedules_skipped_no_doctor = 0
        self.schedules_skipped_duplicate = 0

    def to_dict(self):
        return {
            "areas_created": self.areas_created,
            "clinics_created": self.clinics_created,
            "schedules_inserted": self.schedules_inserted,
            "schedules_updated": self.schedules_updated,
            "schedules_skipped_no_doctor": self.schedules_skipped_no_doctor,
            "schedules_skipped_duplicate": self.schedules_skipped_duplicate,
        }

async def _get_or_create_area(db: AsyncSession, name: str) -> int:
    if not name:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="院区名称为空")
    res = await db.execute(select(HospitalArea).where(HospitalArea.name == name))
    area = res.scalars().first()
    if area:
        return area.area_id
    area = HospitalArea(name=name)
    db.add(area)
    await db.flush()
    return area.area_id

async def _get_or_create_clinic(db: AsyncSession, area_id: int, name: str) -> int:
    res = await db.execute(select(Clinic).where(and_(Clinic.area_id == area_id, Clinic.name == name)))
    clinic = res.scalars().first()
    if clinic:
        return clinic.clinic_id
    clinic = Clinic(area_id=area_id, name=name)
    db.add(clinic)
    await db.flush()
    return clinic.clinic_id

async def _find_doctor_id_by_name(db: AsyncSession, name: str) -> int | None:
    if not name:
        return None
    res = await db.execute(select(Doctor).where(Doctor.name == name))
    doc = res.scalars().first()
    return doc.doctor_id if doc else None

async def import_all_json(db: AsyncSession) -> Dict[str, Any]:
    """读取 all.json（顶层数组结构）并导入排班数据。"""
    if not ALL_JSON_PATH.exists():
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="all.json 文件不存在，请先运行爬虫合并流程")
    try:
        raw = json.loads(ALL_JSON_PATH.read_text("utf-8"))
    except Exception as e:
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg=f"解析 all.json 失败: {e}")

    stats = CrawlerImportStats()
    stats.schedules_updated = 0  # 新增：更新计数
    seen_schedule_keys = set()  # 防重复 (doctor_id, clinic_id, date, time_section)

    for top_item in raw:
        data_list = top_item.get("data", [])
        for area_clinic_item in data_list:
            area_name = (area_clinic_item.get("hosAreaName") or "").strip() or "未知院区"
            clinic_name = (area_clinic_item.get("clinicName") or "").strip() or "未知门诊"

            area_id = await _get_or_create_area(db, area_name)
            if area_id:
                stats.areas_created += 1 if False else 0  # 创建计数在函数中返回后无法区分是否新建，保持0或可后续改进
            clinic_id = await _get_or_create_clinic(db, area_id, clinic_name)
            if clinic_id:
                stats.clinics_created += 1 if False else 0

            week_schedule = area_clinic_item.get("weekSchedule", [])
            for sched in week_schedule:
                doctor_name = (sched.get("doctorName") or "").strip()
                doctor_id = await _find_doctor_id_by_name(db, doctor_name)
                if not doctor_id:
                    stats.schedules_skipped_no_doctor += 1
                    continue

                date_str = (sched.get("curDayTime") or "").strip()
                time_section = (sched.get("timeSectionName") or "").strip() or "未知"
                week_day = int(sched.get("curDate") or 0) or 0
                try:
                    from datetime import datetime
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    stats.schedules_skipped_duplicate += 1
                    continue

                price_raw = sched.get("chargePrice", 0)
                try:
                    price_val = float(price_raw)
                except Exception:
                    price_val = 0.0
                total_slots = int(sched.get("signalSum") or 0)
                remaining_slots = total_slots
                stop_flag = str(sched.get("stopFlag", "0"))
                status = "停诊" if stop_flag == "1" else "正常"
                cons_type_name = (sched.get("consTypeDicCodeName") or "").lower()
                clinic_name_lower = clinic_name.lower()
                if "特需" in clinic_name_lower:
                    slot_type = SlotType.SPECIAL
                elif ("专家" in cons_type_name) or ("知名" in cons_type_name):
                    slot_type = SlotType.EXPERT
                else:
                    slot_type = SlotType.NORMAL

                key = (doctor_id, clinic_id, date_obj, time_section)
                if key in seen_schedule_keys:
                    stats.schedules_skipped_duplicate += 1
                    continue
                seen_schedule_keys.add(key)

                # 检查数据库中是否已存在该排班（按唯一约束字段匹配）
                existing = await db.execute(
                    select(Schedule).where(
                        and_(
                            Schedule.doctor_id == doctor_id,
                            Schedule.clinic_id == clinic_id,
                            Schedule.date == date_obj,
                            Schedule.time_section == time_section,
                            Schedule.slot_type == slot_type
                        )
                    )
                )
                existing_schedule = existing.scalars().first()

                if existing_schedule:
                    # 更新已有排班记录
                    existing_schedule.total_slots = total_slots
                    existing_schedule.remaining_slots = remaining_slots
                    existing_schedule.status = status
                    existing_schedule.price = price_val
                    existing_schedule.week_day = week_day
                    stats.schedules_updated += 1
                else:
                    # 插入新排班记录
                    schedule = Schedule(
                        doctor_id=doctor_id,
                        clinic_id=clinic_id,
                        date=date_obj,
                        week_day=week_day,
                        time_section=time_section,
                        slot_type=slot_type,
                        total_slots=total_slots,
                        remaining_slots=remaining_slots,
                        status=status,
                        price=price_val
                    )
                    db.add(schedule)
                    stats.schedules_inserted += 1

    await db.commit()
    return stats.to_dict()

__all__ = ["import_all_json", "crawl_and_import_schedules"]


def _get_current_week_info() -> str:
    """返回当前周信息，格式: 年份i周(开始-结束)"""
    today = datetime.date.today()
    week_number = today.isocalendar()[1]
    year = today.isocalendar()[0]
    start_of_week = today - datetime.timedelta(days=today.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)
    return f"{year}年{week_number}周({start_of_week.strftime('%m.%d')}-{end_of_week.strftime('%m.%d')})"


async def _fetch_doctor_schedule(session: aiohttp.ClientSession, doctor_id: str, doctor_name: str) -> Optional[Dict]:
    """爬取单个医生的排班数据"""
    try:
        url = f"{SCHEDULE_URL}{doctor_id}"
        async with session.get(url, headers=HEADERS, ssl=False, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                data = await response.json()
                return data
            else:
                logger.warning(f"获取医生 {doctor_name} 排班失败，状态码: {response.status}")
                return None
    except Exception as e:
        logger.error(f"爬取医生 {doctor_name} 排班异常: {e}")
        return None


async def _crawl_all_schedules() -> Dict[str, Any]:
    """爬取所有医生的排班数据"""
    if not CRAWLER_DATA_PATH.exists():
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"医生数据文件不存在: {CRAWLER_DATA_PATH}"
        )
    
    # 读取医生列表
    doctor_list = json.loads(CRAWLER_DATA_PATH.read_text("utf-8"))
    week_info = _get_current_week_info()
    output_dir = SCHEDULE_FOLDER / week_info
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"开始爬取 {len(doctor_list)} 位医生的排班数据")
    
    # 创建异步HTTP会话
    connector = aiohttp.TCPConnector(ssl=False, limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for doc in doctor_list:
            doctor_id = doc.get("document_id", "00000")
            doctor_name = doc.get("doctor_name", "未知")
            if doctor_id == "00000":
                continue
            tasks.append(_fetch_doctor_schedule(session, doctor_id, doctor_name))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 保存结果
    success_count = 0
    all_schedules = []
    for idx, result in enumerate(results):
        if isinstance(result, dict) and result:
            doctor_name = doctor_list[idx].get("doctor_name", f"doctor_{idx}")
            file_path = output_dir / f"{doctor_name}.json"
            file_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            all_schedules.append(result)
            success_count += 1
    
    logger.info(f"爬取完成：成功 {success_count}/{len(doctor_list)}")
    return {"success": success_count, "total": len(doctor_list), "output_dir": str(output_dir)}


def _merge_schedule_files() -> int:
    """合并所有排班JSON文件为 all.json"""
    week_info = _get_current_week_info()
    schedule_dir = SCHEDULE_FOLDER / week_info
    
    if not schedule_dir.exists():
        raise BusinessHTTPException(
            code=settings.DATA_GET_FAILED_CODE,
            msg=f"排班目录不存在: {schedule_dir}"
        )
    
    all_schedules = []
    for json_file in schedule_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text("utf-8"))
            all_schedules.append(data)
        except Exception as e:
            logger.warning(f"跳过无效文件 {json_file.name}: {e}")
    
    # 保存到根目录
    ALL_JSON_PATH.write_text(json.dumps(all_schedules, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"合并完成：{len(all_schedules)} 个排班文件 -> all.json")
    return len(all_schedules)


async def crawl_and_import_schedules(db: AsyncSession, skip_crawl: bool = False) -> Dict[str, Any]:
    """完整流程：爬取 -> 合并 -> 导入数据库
    
    Args:
        db: 数据库会话
        skip_crawl: 是否跳过爬虫步骤（直接使用已有 all.json）
    
    Returns:
        包含爬取统计和导入统计的字典
    """
    result = {
        "crawl_stats": None,
        "merge_count": 0,
        "import_stats": None
    }
    
    try:
        # 步骤1：爬取排班数据（可选跳过）
        if not skip_crawl:
            logger.info("步骤 1/3: 开始爬取排班数据")
            crawl_stats = await _crawl_all_schedules()
            result["crawl_stats"] = crawl_stats
            
            # 步骤2：合并JSON文件
            logger.info("步骤 2/3: 合并排班文件")
            merge_count = _merge_schedule_files()
            result["merge_count"] = merge_count
        else:
            logger.info("跳过爬取步骤，直接使用现有 all.json")
            if not ALL_JSON_PATH.exists():
                raise BusinessHTTPException(
                    code=settings.DATA_GET_FAILED_CODE,
                    msg="all.json 不存在且未启用爬取，请先生成数据文件"
                )
        
        # 步骤3：导入数据库
        logger.info("步骤 3/3: 导入数据库")
        import_stats = await import_all_json(db)
        result["import_stats"] = import_stats
        
        return result
        
    except Exception as e:
        logger.error(f"爬虫导入流程失败: {e}")
        raise


__all__ = ["import_all_json", "crawl_and_import_schedules"]
