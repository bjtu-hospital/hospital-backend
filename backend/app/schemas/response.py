
from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel
from pydantic.generics import GenericModel
from app.schemas.user import user, Token

T = TypeVar("T")

# ====== 全局异常相关返回类型 ======
class UnknownErrorResponse(BaseModel):
    error: str
    detail: str

class HTTPErrorResponse(BaseModel):
    error: str
    detail: str

class RequestValidationErrorResponse(BaseModel):
    error: str
    detail: list

class AuthErrorResponse(BaseModel):
    error: str
    msg: str

class TrafficErrorResponse(BaseModel):
    error: str
    msg: str



# ====== AUTH认证模块相关返回类型 ======


# 登录成功返回的数据模型
class LoginResponse(BaseModel):
    userid: int
    access_token: str
    token_type: str
    user_type: str  # 用户类型：student, teacher, doctor, admin, external

# 获取所有用户返回的数据模型
class UsersListResponse(BaseModel):
    users: List[user]

# 获取单个用户返回的数据模型
class SingleUserResponse(BaseModel):
    user: user

# 删除成功返回的数据模型
class DeleteResponse(BaseModel):
    detail: str

# 注册成功返回的数据模型
class RegisterResponse(BaseModel):
    detail: str

# Token失效返回的数据模型
class TokenErrorResponse(BaseModel):
    error: str

# 更新用户信息返回的数据模型
class UpdateUserResponse(BaseModel):
    user: user

# 获取当前用户角色返回的数据模型
class UserRoleResponse(BaseModel):
    role: str

# 更新用户角色返回的数据模型
class UpdateUserRoleResponse(BaseModel):
    detail: str

# ====== Traffic交通模块相关返回类型 ======

# 流量数据项
class FlowDataItem(BaseModel):
    current_step: int
    velocity_prediction: float

class MeasurementDataItem(BaseModel):
    record_id: int
    time: int
    velocity_record: float

# 单个测量数据项
class SingleMeasurementDataItem(BaseModel):
    time: int
    velocity_record: float

# 位置预测数据
class LocationPredictionData(BaseModel):
    location_id: int
    flow_data: List[FlowDataItem]

class LocationMeasurementData(BaseModel):
    location_id: int
    flow_data: list[MeasurementDataItem]

# 场景预测响应
class ScenePredictionsResponse(BaseModel):
    info: str
    start_time: int
    step: int
    predictions: List[LocationPredictionData]

# 单个位置预测响应
class LocationPredictionResponse(BaseModel):
    info: str
    location_id: int
    start_time: int
    step: int
    flow_data: List[FlowDataItem]

class SceneMeasurementsResponse(BaseModel):
    info: str
    start_time: int
    step: int
    measurements: list[LocationMeasurementData]

class LocationMeasurementResponse(BaseModel):
    info: str
    location_id: int
    start_time: int
    step: int
    flow_data: list[MeasurementDataItem]

# 单个测量记录响应
class SingleMeasurementResponse(BaseModel):
    info: str
    location_id: int
    record_id: int
    flow_data: SingleMeasurementDataItem

# 位置信息
class LocationInfo(BaseModel):
    location_id: int
    longitude: float
    latitude: float

# 位置列表响应
class LocationsListResponse(BaseModel):
    info: str
    locations: List[LocationInfo]

# 单个位置响应
class SingleLocationResponse(BaseModel):
    info: str
    location_id: int
    longitude: float
    latitude: float

# 场景信息
class SceneInfo(BaseModel):
    scene_id: int
    name: str
    description: str
    step_length: int
    area: str
    area_id: int
    measurement_start_time: int = None
    measurement_end_time: int = None

# 场景详情
class SceneDetail(BaseModel):
    scene_id: int
    name: str
    area: str

# 场景列表响应
class ScenesListResponse(BaseModel):
    info: str
    scenes: List[SceneInfo]

# 单个场景响应
class SingleSceneResponse(BaseModel):
    info: str
    scene_id: int
    name: str
    area: str
    area_id: int
    step_length: int
    measurement_start_time: int = None
    measurement_end_time: int = None

# 图边信息
class GraphEdge(BaseModel):
    edge_id: int
    start_vertex: int
    end_vertex: int

# 图结构响应
class GraphResponse(BaseModel):
    graph: List[GraphEdge]

# 单个图边响应
class SingleGraphEdgeResponse(BaseModel):
    graph_data: GraphEdge

# 通用响应模型
class ResponseModel(GenericModel, Generic[T]):
    code: int
    message: Optional[T]
    
    

        

# ====== User Access Log相关返回类型 ======
class UserAccessLogItem(BaseModel):
    user_access_log_id: int
    user_id: int | None = None
    ip: str
    ua: str | None = None
    url: str
    method: str
    status_code: int
    response_code: int | None = None
    access_time: str  # ISO格式字符串
    duration_ms: int

    class Config:
        orm_mode = True

class UserAccessLogPageResponse(BaseModel):
    logs: list[UserAccessLogItem]
    total: int
    total_pages: int
    page: int
    page_size: int

        

        

# ====== Traffic Light相关返回类型 ======
from typing import List
class TrafficLightCoordinates(BaseModel):
    N_latitude: Optional[float]
    N_longitude: Optional[float]
    E_latitude: Optional[float]
    E_longitude: Optional[float]
    S_latitude: Optional[float]
    S_longitude: Optional[float]
    W_latitude: Optional[float] 
    W_longitude: Optional[float]

class TrafficLightItem(BaseModel):
    light_id: int
    coordinates: TrafficLightCoordinates

class TrafficLightsListResponse(BaseModel):
    scene_id: int
    lights_data: List[TrafficLightItem]

class SingleTrafficLightResponse(BaseModel):
    light_id: int
    coordinates: TrafficLightCoordinates
    area_id: int

class TrafficLightStatusHistoryItem(BaseModel):
    current_step: int
    status: int

class TrafficLightHistoryItem(BaseModel):
    light_id: int
    coordinates: TrafficLightCoordinates
    status_history: List[TrafficLightStatusHistoryItem]

class TrafficLightsStatusesResponse(BaseModel):
    scene_id: int
    start_time: int
    step: int
    step_length: int
    history: List[TrafficLightHistoryItem]

class TrafficLightStatusItem(BaseModel):
    light_id: int
    coordinates: TrafficLightCoordinates
    status: int

class TrafficLightsStatusAtTimeResponse(BaseModel):
    scene_id: int
    time: int
    lights_data: List[TrafficLightStatusItem]

class SingleTrafficLightStatusesResponse(BaseModel):
    light_id: int
    scene_id: int
    start_time: int
    step: int
    step_length: int
    coordinates: TrafficLightCoordinates
    history: List[TrafficLightStatusHistoryItem]

class SingleTrafficLightStatusAtTimeResponse(BaseModel):
    light_id: int
    scene_id: int
    time: int
    coordinates: TrafficLightCoordinates
    status: int

        

        

class PasswordChangeRequestResponse(BaseModel):
    detail: str

class PasswordChangeConfirmResponse(BaseModel):
    detail: str
        

        
# 静态异常响应模型
class StatisticsErrorResponse(BaseModel):
    msg: str = "统计数据获取失败"

# 用户统计响应
class UserStatisticsResponse(BaseModel):
    total_users: int

# 节点统计响应
class LocationStatisticsResponse(BaseModel):
    total_locations: int

class VisitStatisticsResponse(BaseModel):
    """
    网站访问量统计响应结构
    """
    total_visits: int
    growth_percent: float
    compare_days: int
    
    
class LoginCountByDayItem(BaseModel):
    day: str
    total_requests: int

class LoginCountByDayResponse(BaseModel):
    days: list[LoginCountByDayItem]


# ====== 管理员管理模块相关返回类型 ======

class AdminRegisterResponse(BaseModel):
    detail: str

class MajorDepartmentListResponse(BaseModel):
    departments: List[dict]

class MinorDepartmentListResponse(BaseModel):
    departments: List[dict]

class DoctorListResponse(BaseModel):
    doctors: List[dict]

class DoctorAccountCreateResponse(BaseModel):
    detail: str
    user_id: int
    doctor_id: int

class DoctorTransferResponse(BaseModel):
    detail: str
    doctor_id: int
    old_dept_id: int
    new_dept_id: int