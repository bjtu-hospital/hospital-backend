"""
PDF生成服务
用于生成病历单PDF文件（参考前端样式设计）
"""
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from datetime import datetime
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class MedicalRecordPDFGenerator:
    """病历单PDF生成器（优化版 - 参考前端样式）"""
    
    # 颜色定义（与前端保持一致）
    COLOR_PRIMARY = HexColor('#1e3a8a')      # 深蓝色（校医院主色）
    COLOR_SECONDARY = HexColor('#c41e3a')    # 红色（印章色）
    COLOR_TEXT_PRIMARY = HexColor('#333333') # 主要文字
    COLOR_TEXT_SECONDARY = HexColor('#666666') # 次要文字
    COLOR_TEXT_LIGHT = HexColor('#888888')   # 浅色文字
    COLOR_BORDER = HexColor('#eeeeee')       # 边框色
    COLOR_BG_DIAGNOSIS = HexColor('#f0f4ff') # 诊断背景色
    
    def __init__(self):
        """初始化PDF生成器，注册中文字体（使用静态资源字体）"""
        self.chinese_font = None
        self.chinese_font_bold = None
        self.font_registered = False
        
        # 获取静态字体目录路径
        service_dir = Path(__file__).parent  # app/services
        backend_dir = service_dir.parent.parent  # backend
        fonts_dir = backend_dir / "static" / "fonts"
        
        logger.info(f"尝试从静态资源加载字体: {fonts_dir}")
        
        # 优先级顺序的字体配置（支持 OTF/TTF/TTC 格式）
        fonts_to_try = [
            # 第一选择：思源黑体 OTF（推荐）
            (fonts_dir / "SourceHanSans-Regular.otf", "SourceHanSans", "思源黑体-Regular (OTF)"),
            (fonts_dir / "SourceHanSans-Bold.otf", "SourceHanSans-Bold", "思源黑体-Bold (OTF)"),
            # 兼容：思源黑体 TTF
            (fonts_dir / "SourceHanSans-Regular.ttf", "SourceHanSans", "思源黑体-Regular (TTF)"),
            (fonts_dir / "SourceHanSans-Bold.ttf", "SourceHanSans-Bold", "思源黑体-Bold (TTF)"),
            # 第二选择：文泉驿正黑体 TTC
            (fonts_dir / "wqy-microhei.ttc", "WQYMicroHei", "文泉驿微米黑"),
            (fonts_dir / "WenQuanYi_12pt.ttc", "WenQuanYi", "文泉驿正黑体"),
        ]
        
        # 尝试加载静态资源字体
        for font_path, font_name, font_display_name in fonts_to_try:
            if font_path.exists():
                try:
                    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
                    self.chinese_font = font_name
                    self.chinese_font_bold = font_name
                    self.font_registered = True
                    logger.info(f"✓ 成功注册静态资源字体: {font_display_name} ({font_path})")
                    break
                except Exception as e:
                    logger.warning(f"✗ 注册字体 {font_display_name} 失败: {e}")
                    continue
        
        # 如果静态资源字体加载失败，尝试回退到系统字体（仅作备选）
        if not self.font_registered:
            logger.warning(f"未找到静态资源字体，尝试回退到系统字体...")
            try:
                import platform
                system = platform.system()
                
                if system == "Darwin":  # macOS
                    mac_fonts = [
                        ("/System/Library/Fonts/PingFang.ttc", "PingFang"),
                        ("/System/Library/Fonts/STHeiti Light.ttc", "STHeiti"),
                        ("/Library/Fonts/Arial Unicode.ttf", "ArialUnicode"),
                    ]
                    for font_path, font_name in mac_fonts:
                        if os.path.exists(font_path):
                            try:
                                pdfmetrics.registerFont(TTFont(font_name, font_path))
                                self.chinese_font = font_name
                                self.chinese_font_bold = font_name
                                self.font_registered = True
                                logger.info(f"✓ 使用系统字体 (macOS): {font_name}")
                                break
                            except Exception as e:
                                logger.warning(f"注册字体 {font_name} 失败: {e}")
                
                elif system == "Windows":
                    win_fonts = [
                        ("C:/Windows/Fonts/msyh.ttc", "MSYH"),
                        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
                        ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
                    ]
                    for font_path, font_name in win_fonts:
                        if os.path.exists(font_path):
                            try:
                                pdfmetrics.registerFont(TTFont(font_name, font_path))
                                self.chinese_font = font_name
                                self.chinese_font_bold = font_name
                                self.font_registered = True
                                logger.info(f"✓ 使用系统字体 (Windows): {font_name}")
                                break
                            except Exception as e:
                                logger.warning(f"注册字体 {font_name} 失败: {e}")
                
                elif system == "Linux":
                    linux_fonts = [
                        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WQY"),
                        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "NotoSans"),
                    ]
                    for font_path, font_name in linux_fonts:
                        if os.path.exists(font_path):
                            try:
                                pdfmetrics.registerFont(TTFont(font_name, font_path))
                                self.chinese_font = font_name
                                self.chinese_font_bold = font_name
                                self.font_registered = True
                                logger.info(f"✓ 使用系统字体 (Linux): {font_name}")
                                break
                            except Exception as e:
                                logger.warning(f"注册字体 {font_name} 失败: {e}")
            except Exception as e:
                logger.error(f"系统字体加载异常: {e}", exc_info=True)
        
        # 如果都失败，使用 Helvetica（会显示方块）
        if not self.font_registered:
            logger.error(
                f"✗ 字体加载完全失败！\n"
                f"  • 静态资源路径: {fonts_dir}\n"
                f"  • 请运行 'python scripts/download_fonts.py' 下载字体文件\n"
                f"  • 或手动下载思源黑体放入上述目录\n"
                f"  • 已降级到 Helvetica（中文会显示为方块）"
            )
            self.chinese_font = 'Helvetica'
            self.chinese_font_bold = 'Helvetica-Bold'
    
    def generate_medical_record(
        self, 
        visit_data: dict, 
        patient_data: dict,
        output_path: str
    ) -> str:
        """
        生成病历单PDF（优化版）
        
        Args:
            visit_data: 就诊记录数据
            patient_data: 患者基本信息
            output_path: PDF输出路径
            
        Returns:
            生成的PDF文件路径
        """
        # 创建画布
        c = canvas.Canvas(output_path, pagesize=A4)
        width, height = A4
        
        # 设置页边距
        margin_left = 20*mm
        margin_right = width - 20*mm
        margin_top = height - 20*mm
        
        # 绘制医院标题区域（带双logo效果）
        y_pos = self._draw_hospital_header(c, width, margin_top)
        
        # 绘制病历单标题
        y_pos = self._draw_record_title(c, width, y_pos)
        
        # 绘制基本信息区
        y_pos = self._draw_info_section(c, patient_data, visit_data, margin_left, margin_right, y_pos)
        
        # 绘制分隔线
        y_pos = self._draw_divider(c, margin_left, margin_right, y_pos)
        
        # 绘制病历内容区块
        y_pos = self._draw_content_sections(c, visit_data, margin_left, margin_right, y_pos)
        
        # 绘制医师签名区
        y_pos = self._draw_signature_section(c, visit_data, margin_left, margin_right, y_pos)
        
        # 绘制医院印章
        self._draw_stamp(c, margin_right, y_pos)
        
        # 保存PDF
        c.save()
        
        return output_path
    
    def _draw_hospital_header(self, c: canvas.Canvas, width: float, y_start: float) -> float:
        """绘制医院标题区域（参考前端 .hospital-header 样式，带双 logo）"""
        y_pos = y_start
        
        # Logo 配置
        logo_height = 20*mm
        logo_y = y_pos - logo_height/2
        
        # 左侧：北京交通大学 logo
        bjtu_logo_path = os.path.join(os.path.dirname(__file__), "../static/images/logo/BJTU-logo.png")
        if os.path.exists(bjtu_logo_path):
            try:
                # 保持纵横比，高度为 20mm
                c.drawImage(bjtu_logo_path, 30*mm, logo_y, 
                           width=logo_height*1.5, height=logo_height, 
                           preserveAspectRatio=True, mask='auto')
            except Exception as e:
                print(f"BJTU logo 加载失败: {e}")
        
        # 右侧：医院 logo
        hospital_logo_path = os.path.join(os.path.dirname(__file__), "../static/images/logo/hospital_logo.png")
        if os.path.exists(hospital_logo_path):
            try:
                # 保持纵横比，高度为 20mm
                c.drawImage(hospital_logo_path, width - 30*mm - logo_height*1.5, logo_y,
                           width=logo_height*1.5, height=logo_height,
                           preserveAspectRatio=True, mask='auto')
            except Exception as e:
                print(f"Hospital logo 加载失败: {e}")
        
        # 医院名称（中文）- 居中显示
        c.setFont(self.chinese_font_bold, 18)
        c.setFillColor(self.COLOR_PRIMARY)
        c.drawCentredString(width/2, y_pos - 5*mm, "北京交通大学校医院")
        
        y_pos -= 12*mm
        
        # 医院名称（英文）
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawCentredString(width/2, y_pos, "Beijing Jiaotong University Hospital")
        
        y_pos -= 8*mm
        
        # 底部边框线（深蓝色）
        c.setStrokeColor(self.COLOR_PRIMARY)
        c.setLineWidth(2)
        c.line(40*mm, y_pos, width - 40*mm, y_pos)
        
        y_pos -= 10*mm
        return y_pos
    
    def _draw_record_title(self, c: canvas.Canvas, width: float, y_pos: float) -> float:
        """绘制病历单标题（参考前端 .record-title 样式）"""
        # 标题文字
        c.setFont(self.chinese_font_bold, 20)
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        
        # 增加字间距效果
        title = "门 诊 病 历"
        c.drawCentredString(width/2, y_pos, title)
        
        y_pos -= 4*mm
        
        # 渐变线效果（用三段线模拟）
        line_y = y_pos
        line_center = width / 2
        line_length = 80*mm
        
        # 左侧渐变（淡色）
        c.setStrokeColor(HexColor('#ffcccc'))
        c.setLineWidth(2)
        c.line(line_center - line_length, line_y, line_center - line_length/3, line_y)
        
        # 中间（红色）
        c.setStrokeColor(self.COLOR_SECONDARY)
        c.setLineWidth(2)
        c.line(line_center - line_length/3, line_y, line_center + line_length/3, line_y)
        
        # 右侧渐变（淡色）
        c.setStrokeColor(HexColor('#ffcccc'))
        c.setLineWidth(2)
        c.line(line_center + line_length/3, line_y, line_center + line_length, line_y)
        
        y_pos -= 10*mm
        return y_pos
    
    def _draw_info_section(self, c: canvas.Canvas, patient_data: dict, visit_data: dict, 
                          left: float, right: float, y_pos: float) -> float:
        """绘制基本信息区（参考前端 .info-section 样式）"""
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        
        line_height = 6*mm
        label_width = 25*mm
        
        # 第一行：姓名、性别、年龄
        y_pos -= line_height
        c.drawString(left, y_pos, "姓名：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + label_width, y_pos, str(patient_data.get("name", "-")))
        
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left + 70*mm, y_pos, "性别：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + 85*mm, y_pos, str(patient_data.get("gender", "-")))
        
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left + 110*mm, y_pos, "年龄：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        age = patient_data.get("age", 0)
        c.drawString(left + 125*mm, y_pos, f"{age}岁")
        
        # 第二行：门诊号、就诊日期
        y_pos -= line_height
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left, y_pos, "门诊号：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + label_width, y_pos, str(patient_data.get("outpatientNo", "-")))
        
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left + 70*mm, y_pos, "就诊日期：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + 95*mm, y_pos, str(patient_data.get("visitDate", "-")))
        
        # 第三行：科室、医生
        y_pos -= line_height
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left, y_pos, "科室：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + label_width, y_pos, str(visit_data.get("department", "-")))
        
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left + 70*mm, y_pos, "医生：")
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.setFont(self.chinese_font_bold, 10)
        c.drawString(left + 85*mm, y_pos, str(visit_data.get("doctorName", "-")))
        
        y_pos -= 8*mm
        return y_pos
    
    def _draw_divider(self, c: canvas.Canvas, left: float, right: float, y_pos: float) -> float:
        """绘制分隔线"""
        c.setStrokeColor(self.COLOR_BORDER)
        c.setLineWidth(1)
        c.line(left, y_pos, right, y_pos)
        y_pos -= 8*mm
        return y_pos
    
    def _draw_content_sections(self, c: canvas.Canvas, visit_data: dict, 
                              left: float, right: float, y_pos: float) -> float:
        """绘制病历内容区块（参考前端 .content-section 样式）"""
        # 主诉
        y_pos = self._draw_content_block(
            c, "主诉", visit_data.get("chiefComplaint", "无"), 
            left, right, y_pos, is_diagnosis=False
        )
        
        # 现病史
        y_pos = self._draw_content_block(
            c, "现病史", visit_data.get("presentIllness", "无"), 
            left, right, y_pos, is_diagnosis=False
        )
        
        # 辅助检查（如果有）
        if visit_data.get("auxiliaryExam"):
            y_pos = self._draw_content_block(
                c, "辅助检查", visit_data.get("auxiliaryExam"), 
                left, right, y_pos, is_diagnosis=False
            )
        
        # 诊断（特殊样式 - 蓝色背景）
        y_pos = self._draw_content_block(
            c, "诊断", visit_data.get("diagnosis", "无"), 
            left, right, y_pos, is_diagnosis=True
        )
        
        # 处方/处置（如果有）
        if visit_data.get("prescription"):
            y_pos = self._draw_content_block(
                c, "处方/处置", visit_data.get("prescription"), 
                left, right, y_pos, is_diagnosis=False
            )
        
        return y_pos
    
    def _draw_content_block(self, c: canvas.Canvas, title: str, content: str, 
                           left: float, right: float, y_pos: float, 
                           is_diagnosis: bool = False) -> float:
        """绘制单个内容区块"""
        # 标题（带圆点图标）
        c.setFont(self.chinese_font_bold, 11)
        c.setFillColor(self.COLOR_PRIMARY)
        
        # 绘制圆点
        c.circle(left + 2*mm, y_pos + 1*mm, 1*mm, fill=1)
        
        # 绘制标题
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        c.drawString(left + 6*mm, y_pos, title)
        
        y_pos -= 6*mm
        
        # 如果是诊断，绘制特殊背景
        if is_diagnosis:
            # 计算内容高度
            lines = self._wrap_text_advanced(c, content, right - left - 20*mm)
            content_height = len(lines) * 5*mm + 8*mm
            
            # 绘制浅蓝色背景
            c.setFillColor(self.COLOR_BG_DIAGNOSIS)
            c.roundRect(left + 8*mm, y_pos - content_height + 2*mm, 
                       right - left - 16*mm, content_height, 
                       3*mm, fill=1, stroke=0)
            
            # 绘制左侧深蓝色竖线
            c.setStrokeColor(self.COLOR_PRIMARY)
            c.setLineWidth(3)
            c.line(left + 8*mm, y_pos - content_height + 2*mm, 
                  left + 8*mm, y_pos + 2*mm)
            
            # 诊断文字（深蓝色、加粗）
            c.setFont(self.chinese_font_bold, 11)
            c.setFillColor(self.COLOR_PRIMARY)
            
            y_pos -= 4*mm
            for line in lines:
                c.drawString(left + 14*mm, y_pos, line)
                y_pos -= 5*mm
            y_pos -= 4*mm
        else:
            # 普通内容
            c.setFont(self.chinese_font, 10)
            c.setFillColor(HexColor('#555555'))
            
            lines = self._wrap_text_advanced(c, content, right - left - 20*mm)
            for line in lines:
                c.drawString(left + 12*mm, y_pos, line)
                y_pos -= 5*mm
        
        y_pos -= 6*mm
        return y_pos
    
    def _draw_signature_section(self, c: canvas.Canvas, visit_data: dict, 
                               left: float, right: float, y_pos: float) -> float:
        """绘制医师签名区（参考前端 .signature-section 样式）"""
        y_pos -= 8*mm
        
        # 上方虚线
        c.setStrokeColor(HexColor('#dddddd'))
        c.setLineWidth(1)
        c.setDash([3, 3])
        c.line(left, y_pos, right, y_pos)
        c.setDash([])  # 恢复实线
        
        y_pos -= 8*mm
        
        # 左侧：医师签名
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(left, y_pos, "医师签名：")
        
        c.setFont(self.chinese_font_bold, 10)
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        doctor_name = visit_data.get("doctorName", "-")
        c.drawString(left + 25*mm, y_pos, doctor_name)
        
        # 绘制下划线
        c.setStrokeColor(self.COLOR_TEXT_PRIMARY)
        c.setLineWidth(0.5)
        c.line(left + 25*mm, y_pos - 1*mm, left + 60*mm, y_pos - 1*mm)
        
        # 右侧：日期
        c.setFont(self.chinese_font, 10)
        c.setFillColor(self.COLOR_TEXT_SECONDARY)
        c.drawString(right - 60*mm, y_pos, "日期：")
        
        c.setFont(self.chinese_font_bold, 10)
        c.setFillColor(self.COLOR_TEXT_PRIMARY)
        visit_date = visit_data.get("visitDate", "-")
        c.drawString(right - 45*mm, y_pos, visit_date)
        
        # 绘制下划线
        c.setStrokeColor(self.COLOR_TEXT_PRIMARY)
        c.setLineWidth(0.5)
        c.line(right - 45*mm, y_pos - 1*mm, right - 10*mm, y_pos - 1*mm)
        
        y_pos -= 15*mm
        return y_pos
    
    def _draw_stamp(self, c: canvas.Canvas, right: float, y_pos: float):
        """绘制医院印章（参考前端 .stamp-area 样式）"""
        # 印章位置（右下角）
        stamp_x = right - 25*mm
        stamp_y = y_pos - 15*mm
        stamp_radius = 20*mm
        
        # 绘制圆形边框
        c.setStrokeColor(self.COLOR_SECONDARY)
        c.setLineWidth(2)
        c.setFillColor(HexColor('#ffffff'))
        c.circle(stamp_x, stamp_y, stamp_radius, fill=0, stroke=1)
        
        # 印章文字
        c.setFont(self.chinese_font_bold, 9)
        c.setFillColor(self.COLOR_SECONDARY)
        
        # 第一行
        c.drawCentredString(stamp_x, stamp_y + 4*mm, "北京交通大学")
        # 第二行
        c.drawCentredString(stamp_x, stamp_y - 2*mm, "校医院")
        
        # 设置透明度效果（通过调整颜色）
        c.setFillColorRGB(0.77, 0.12, 0.23, alpha=0.6)
    
    def _wrap_text_advanced(self, c: canvas.Canvas, text: str, max_width: float) -> list:
        """
        高级文本换行（支持中文，更智能）
        
        Args:
            c: Canvas对象
            text: 要换行的文本
            max_width: 最大宽度
            
        Returns:
            换行后的文本列表
        """
        if not text or text == "无":
            return ["无"]
        
        lines = []
        current_line = ""
        
        # 按字符处理（适合中文）
        for char in text:
            test_line = current_line + char
            text_width = c.stringWidth(test_line, self.chinese_font, 10)
            
            if text_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        
        if current_line:
            lines.append(current_line)
        
        return lines if lines else ["无"]


def ensure_pdf_directory():
    """确保PDF存储目录存在（存储在app/static之外，防止直接访问）"""
    pdf_dir = Path("app/static/pdf/medical_records")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    return pdf_dir
