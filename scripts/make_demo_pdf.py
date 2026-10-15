"""Build the three-page synthetic source PDF used only for UI demonstration.

Requires reportlab for regeneration; the delivered application uses the saved PDF.
No scientific observations or publications are represented by this fixture.
"""
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parents[1]
FONT = Path('/System/Library/Fonts/STHeiti Light.ttc')
pdfmetrics.registerFont(TTFont('PlantCN', str(FONT), subfontIndex=0))
OUT = ROOT / 'backend/demo-assets/seedling-paper.pdf'
WIDTH, HEIGHT = A4
c = canvas.Canvas(str(OUT), pagesize=A4, pageCompression=1)
c.setTitle('拟南芥光照实验 - 合成演示原件')
c.setAuthor('植物科研数据工坊 · 合成演示')

def text(x,y,value,size=11,color='#25364B'):
    c.setFillColor(HexColor(color));c.setFont('PlantCN',size);c.drawString(x,y,value)

def lines(y, values, size=11, leading=23):
    for value in values:
        text(52,y,value,size);y-=leading
    return y

def start(number, title):
    text(52,HEIGHT-45,'植物科研数据工坊  /  原始文献对照示例',10,'#617B84')
    text(52,HEIGHT-74,'合成演示数据，不作为真实科研结论',13,'#9B651B')
    c.setStrokeColor(HexColor('#C5D6D9'));c.line(52,HEIGHT-88,WIDTH-52,HEIGHT-88)
    text(52,HEIGHT-132,title,20)
    text(52,42,'SYNTHETIC DEMO - NOT A REAL RESEARCH PAPER',8,'#6A7E86')
    text(WIDTH-73,42,str(number),9,'#6A7E86')

start(1,'1  光照处理与叶面积记录')
lines(HEIGHT-178,[
    '拟南芥幼苗合成演示设 8 h、12 h、16 h 三组光照，每组虚构 n=6。',
    '叶面积单位为 mm²。没有真实实验记录或文献。',
])
text(52,HEIGHT-275,'文献使用说明',13)
lines(HEIGHT-307,[
    '本文件专门用于检验原始 PDF、OCR 页面和整理结果的对照体验。',
    '此处的植物、实验分组、样本量与数值均为虚构演示内容。',
    '该示例无真实作者、期刊或 DOI，不得作为科研引用或研究证据。',
    '第 2 页保留合成表格与图像，第 3 页说明推断边界。',
])
c.showPage()

start(2,'结果记录（前章续页）')
text(52,HEIGHT-173,'表 1  合成光照与叶面积记录',12)
top=HEIGHT-193
headers=['光照时长 (h)','合成平均叶面积 (mm²)']
rows=[headers,['8','12.4'],['12','18.2'],['16','21.6']]
for i,row in enumerate(rows):
    y=top-34*i
    c.setFillColor(HexColor('#EAF2F2' if i==0 else '#FFFFFF'))
    c.rect(52,y-34,WIDTH-104,34,fill=1,stroke=0)
    c.setStrokeColor(HexColor('#D4DEDF'));c.line(52,y-34,WIDTH-52,y-34)
    text(64,y-23,row[0],10);text(260,y-23,row[1],10)
text(52,HEIGHT-352,'数值仅用于界面与数据加工测试，不说明真实生物学效应。',10)
c.drawImage(str(ROOT/'backend/demo-assets/seedling-growth.png'),52,145,width=WIDTH-104,height=(WIDTH-104)*600/1080,preserveAspectRatio=True,mask='auto')
text(52,117,'图 1  合成光照与叶面积图；原始图像与表 1 使用相同虚构数值。',10)
c.showPage()

start(3,'3  讨论与边界')
lines(HEIGHT-183,[
    '没有误差、原始测量值或统计检验，不得由演示均值推断显著性、',
    '因果关系或推广真实科研结论。',
    '',
    '整理语料时，应保留数值、单位、适用条件及来源页。',
    '若原文信息缺失，应标注“待确认”，不应由模型补写科研事实。',
    '',
    '本演示的所有数据、图片和材料仅用于软件流程测试。',
])
c.showPage();c.save()
print(OUT)
