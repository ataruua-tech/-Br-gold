"""Standalone exportable chart and concise PDF of the verified scenario result."""
import json
import os
from datetime import datetime
from pathlib import Path
from html import escape

os.environ.setdefault('MPLCONFIGDIR','/tmp/br-gold-matplotlib-cache')
os.environ.setdefault('XDG_CACHE_HOME','/tmp/br-gold-font-cache')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak

from finalize_scenario_study_v2 import OUT,DEST,fmt


def render():
    result=json.loads((OUT/'final-results.json').read_text())
    curves=json.loads((OUT/'final-primary-curves.json').read_text())
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    fig,ax=plt.subplots(figsize=(11.5,6.1))
    palette={'S0':'#1565c0','S1':'#ef6c00','S2':'#c62828','S4':'#6a1b9a','S5':'#00897b'}
    for sid,rows in curves.items():
        if rows:ax.plot([datetime.fromisoformat(r['day']) for r in rows],[r['equity_R'] for r in rows],label=sid,color=palette[sid],linewidth=1.7)
    ax.axhline(0,color='#777777',linewidth=.8)
    ax.set_title('BR/GOLD: сценарный результат после затрат\nЗадержка 18 минут; одинаковый риск на сделку',loc='left',pad=16)
    ax.set_ylabel('Накопленный результат, R')
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3));ax.xaxis.set_major_formatter(mdates.DateFormatter('%m.%Y'))
    ax.grid(alpha=.2);ax.spines[['top','right']].set_visible(False);ax.legend(ncol=5,loc='lower left',frameon=False)
    fig.text(.105,.025,'S3: 0 сделок. Кривые — дневные срезы; табличная просадка рассчитана по минутным переоценкам.\nR не переводится в рубли или проценты капитала без подтверждённых экономических входов.',fontsize=9,color='#444444')
    fig.subplots_adjust(left=.105,right=.98,top=.86,bottom=.17)
    image=DEST/'SCENARIO_EQUITY_R.png';fig.savefig(image,dpi=180);plt.close(fig)
    pdfmetrics.registerFont(TTFont('DV','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('DV-Bold','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    styles=getSampleStyleSheet()
    normal=ParagraphStyle('N',fontName='DV',fontSize=9,leading=12,spaceAfter=7)
    title=ParagraphStyle('T',fontName='DV-Bold',fontSize=17,leading=22,spaceAfter=14,textColor=colors.HexColor('#17324d'))
    heading=ParagraphStyle('H',fontName='DV-Bold',fontSize=11,leading=15,spaceAfter=8,spaceBefore=8)
    small=ParagraphStyle('S',fontName='DV',fontSize=7.6,leading=10,spaceAfter=6)
    story=[]
    def p(text,style=normal):story.append(Paragraph(escape(text),style))
    def table(data,widths):
        t=Table([[Paragraph(escape(str(x)),small) for x in row] for row in data],colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7eef5')),('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#17324d')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f5f7fa')]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        story.append(t);story.append(Spacer(1,10))
    p('BR/GOLD: проверка шести стратегий',title)
    p('Ни один вариант не прошёл заранее записанный фильтр устойчивости.',heading)
    p('Сравнение завершено в согласованных сценарных границах. Обоснованного кандидата для новой виртуальной недели нет. Оптимизация не запускалась. Существующий бот и прежний эксперимент сохранены.')
    p('Обучение: 2023–2024 с последующим расширением прошлого. Проверка: восемь квартальных отрезков 2025–2026 с исключением пяти дней у границ. Вся эта история уже просматривалась; независимого исторического holdout нет.')
    p('Базовый агент: вход после закрытия H1 + 18 минут; спред 2 тика, проскальзывание 2 тика за сторону. Расходы — фиксированный сценарий 0,015% брокеру + 0,0132% бирже за сторону. Человек: ещё 60 секунд реакции.')
    data=[['Вариант','Сделки','net R','Средний R','PF','Просадка R']]
    for row in result['comparison']:
        m=row['agent']['metrics'];data.append([row['strategy']+' '+row['name'],m['trades'],fmt(m['net_R']),fmt(m['mean_net_R'],3),fmt(m['profit_factor'],3),fmt(m['max_drawdown_R'])])
    table(data,[190,48,59,64,49,65])
    p('1R — модельный начальный риск сделки до стопа с заданными расходами. Сравнивается фиксированный R-портфель; это не доходность в процентах и не рублёвый результат. Для S3 нет принятых входов, поэтому ожидание сделки не оценивается.',small)
    p('Чувствительность к исполнению',heading)
    data=[['Вариант','Агент, R','Человек, R','Стресс, R','2025, R','2026, R']]
    for row in result['comparison']:
        a=row['agent'];data.append([row['strategy'],fmt(a['metrics']['net_R']),fmt(row['human']['metrics']['net_R']),fmt(row['stress']['metrics']['net_R']),fmt(a['years']['2025']['net_R']),fmt(a['years']['2026']['net_R'])])
    table(data,[75,80,80,80,80,80])
    p('Стресс: спред 5 тиков, проскальзывание 5 тиков, комиссии ×2. S0 имеет отдельные положительные варианты задержки, но базовый сценарий отрицателен и знак неустойчив. Выбирать удачный вариант после просмотра таблицы нельзя считать независимой проверкой.',small)
    story.append(PageBreak())
    p('Кривые и практические ограничения',title)
    story.append(Image(str(image),width=480,height=255))
    p('Почему денежные результаты не подтверждены',heading)
    p('Исторических исполнимых BID/OFFER и полного подтверждённого ряда экономики контрактов нет. В 2023 году ГО неизвестно во всех 228 279 строках расширенного TradeStats; пропуски есть и позднее. Нынешнее ГО, плечо и экономика не подставлялись в прошлое. Рублёвый результат, целые размеры позиций, нагрузка на капитал и процентная просадка остаются BLOCKED_DATA.')
    p('Проверка и исправление S2',heading)
    p('Рассчитаны 192 итоговых случая и 32 сохранённых случая прежнего S2 — всего 224. Первый журнал выявил один ночной перенос внутридневного выхода S2. Исправление зарегистрировано до пересчёта: выход перед окончанием непрерывных торгов; в исправленном базовом S2 ночных переносов нет. Остальные пять вариантов не менялись.')
    p('Проверены 208 обучений S3, временные границы меток, согласованность сделок с годами/активами, отсутствие пересечений позиций одного актива и входов до доступности данных. Пройдены 15 контрольных тестов. Полный старый объём перебора неизвестен; глобальная статистическая значимость не заявляется.')
    p('Когда должен приходить сигнал',heading)
    p('Условие S0 относится к закрытой H1. Для 10:00–11:00 МСК действующий буфер допускает обработку новой свечи примерно с 11:18, затем следуют очередь GitHub, расчёт, сохранение журнала и Telegram. Это не гарантированный срок доставки человеку. В двух примерах сводки приняты Telegram через 19 мин 23 с и 59 мин 24 с после закрытия H1.')
    p('В отдельно проверенных окнах 27.10.2026 стабильный относительно +30-минутной проверки состав H1 наблюдался через 4,15–62,58 секунды. Кампания имеет лишь три полных окна одной даты при заданном минимуме 12 окон и трёх дат. 18 минут не доказаны как обязательное свойство AlgoPack; четыре минуты — только предварительный кандидат для измерений. Рабочий буфер не менялся.')
    p('Дальнейшее решение',heading)
    p('Новую виртуальную неделю по этим результатам запускать не на чем. Точная денежная проверка требует недостающих входов; идущая неделя продолжает собирать операционные наблюдения. Реальные сделки и платная инфраструктура не подключались.')
    p('Подробности, источники, шесть паспортов, все сценарии, журналы и правила воспроизведения переданы отдельными файлами. Старые +47,51% и +36,67% не подтверждены этой проверкой.',small)
    def page(canvas,doc):
        canvas.setFont('DV',8);canvas.setFillColor(colors.HexColor('#667788'));canvas.drawString(40,24,'BR/GOLD · сценарное исследование · 28.10.2026');canvas.drawRightString(A4[0]-40,24,str(doc.page))
    SimpleDocTemplate(str(DEST/'FINAL_REPORT_RU.pdf'),pagesize=A4,rightMargin=40,leftMargin=40,topMargin=35,bottomMargin=40).build(story,onFirstPage=page,onLaterPages=page)
    print(DEST/'FINAL_REPORT_RU.pdf')


if __name__=='__main__':render()
