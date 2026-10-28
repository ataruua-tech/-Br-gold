"""Validate completed runs, compose the six-family report, and export separate files."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import shutil
from datetime import datetime,timezone
from pathlib import Path

from freeze_six_strategy_study import canonical
from scenario_features_v2 import ROOT,timestamp,protocol

OUT=ROOT/'research/scenario-study-v2'
DEST=ROOT.parent/'artifacts/BR_GOLD_Six_Strategies_V1'
NAMES={'S0':'Donchian H1/H4','S1':'Возврат к среднему','S2':'Диапазон открытия v2.1','S3':'Временной эффект','S4':'Ротация BR/GOLD','S5':'Chaikin Money Flow'}


def verify():
    basepath=OUT/'results/scenario-v2-run001';corpath=OUT/'results/s2-session-correction-v21'
    base=json.loads((basepath/'results.json').read_text());cor=json.loads((corpath/'results.json').read_text());p=protocol()
    expected={(f'S{i}',s['id']) for i in range(6) for s in p['scenario_design']}
    if len(base['cells'])!=192 or {(r['strategy'],r['scenario']) for r in base['cells']}!=expected:raise ValueError('Incomplete original192-cell comparison')
    if len(cor['cells'])!=32 or {r['scenario'] for r in cor['cells']}!={s['id'] for s in p['scenario_design']}:raise ValueError('Incomplete32-cell correction')
    if base['source_data_hash']!=cor['source_data_hash']:raise ValueError('Correction changed datasets')
    cells=[r for r in base['cells'] if r['strategy']!='S2']+cor['cells']
    if len(cells)!=192 or any(r['unresolved_setups'] for r in cells):raise ValueError('Unresolved price outcomes must not enter a complete comparison')
    fits=[json.loads(line) for line in (basepath/'S3-training-fits.jsonl').read_text().splitlines()]
    if len(fits)!=208 or any(r['train_label_end_max'] is not None and r['train_label_end_max']>r['train_cutoff'] for r in fits):raise ValueError('Invalid training cutoff or fit accounting')
    trades=json.loads((basepath/'primary-trades.json').read_text())
    trades['S2']=json.loads((corpath/'primary-trades.json').read_text())
    curves=json.loads((basepath/'primary-curves.json').read_text())
    curves['S2']=json.loads((corpath/'primary-curve.json').read_text())
    indexed={(r['strategy'],r['scenario']):r for r in cells}
    checks=[]
    for strategy,rows in trades.items():
        b=indexed[(strategy,p['primary_agent'])]
        if len(rows)!=b['metrics']['trades']:raise ValueError('Trade-count mismatch')
        if rows and not math.isclose(sum(t['net_R'] for t in rows),b['metrics']['net_R'],abs_tol=1e-8):raise ValueError('Trade/metric netR mismatch')
        if rows and not math.isclose(sum(b['years'][y]['net_R'] or 0 for y in ('2025','2026')),b['metrics']['net_R'],abs_tol=1e-8):raise ValueError('Year contributions mismatch')
        if rows and not math.isclose(sum(b['assets'][y]['net_R'] or 0 for y in ('BR','GOLD')),b['metrics']['net_R'],abs_tol=1e-8):raise ValueError('Asset contributions mismatch')
        for asset in ('BR','GOLD'):
            ordered=sorted([t for t in rows if t['asset']==asset],key=lambda t:t['entry_time'])
            if any(a['exit_time']>b['entry_time'] for a,b in zip(ordered,ordered[1:])):raise ValueError('Overlapping same-asset positions')
        for t in rows:
            if t['entry_time']<t['signal_end']+18:raise ValueError('Entry before declared source availability')
            d=t['direction'];tick=.01 if t['asset']=='BR' else .1
            # Independent accounting identity in native quote units, with disclosed fixed costs.
            denominator=abs(t['entry']-t['stop'])+.02 if t['asset']=='BR' else abs(t['entry']-t['stop'])+.2
            denominator+=(abs(t['entry'])+abs(t['stop']))*.000282
            net=d*(t['exit']-t['entry'])-(abs(t['entry'])+abs(t['exit']))*.000282
            if not math.isclose(net/denominator,t['net_R'],abs_tol=1e-8):raise ValueError('Independent fee/R identity failed')
            if strategy=='S2' and t['entry_time']//1440!=t['exit_time']//1440:raise ValueError('Intraday ORB held overnight')
        checks.append({'strategy':strategy,'trade_rows_reconciled':len(rows),'same_asset_overlap':False,'entry_before_information_time':False})
    return p,base,cor,cells,trades,curves,checks


def fmt(value,digits=2):return '—' if value is None else f'{value:.{digits}f}'


def build():
    p,base,cor,cells,trades,curves,checks=verify();DEST.mkdir(parents=True,exist_ok=True)
    indexed={(r['strategy'],r['scenario']):r for r in cells};rows=[]
    for i in range(6):
        sid=f'S{i}';agent=indexed[(sid,p['primary_agent'])];human=indexed[(sid,p['primary_human'])];stress=indexed[(sid,'stress-r0')];late=indexed[(sid,'d40-r60-s2-l2-f1')]
        requirements={'at_least50':agent['metrics']['trades']>=50,'positive2025':(agent['years']['2025']['net_R'] or 0)>0,'positive2026':(agent['years']['2026']['net_R'] or 0)>0,'positive_human':(human['metrics']['net_R'] or 0)>0,'positive_stress':(stress['metrics']['net_R'] or 0)>0,'positive_delay40_human':(late['metrics']['net_R'] or 0)>0,'lower90_meanR_positive':(agent.get('bootstrap',{}).get('lower90_mean_R') or 0)>0}
        rows.append({'strategy':sid,'name':NAMES[sid],'version':'session-correction-v21' if sid=='S2' else 'scenario-v2','status':'NO_ACCEPTED_SIGNALS' if not agent['metrics']['trades'] else 'NO_ROBUST_EDGE_ESTABLISHED' if not all(requirements.values()) else 'PRICE_LOGIC_SCREEN_PASSED','failed_screen':[k for k,v in requirements.items() if not v],'agent':agent,'human':human,'stress':stress,'delay40_human':late,'net_pnl_rub':None,'net_return_pct':None,'monetary_status':'BLOCKED_DATA','live_promotion':False})
    if any(r['status']=='PRICE_LOGIC_SCREEN_PASSED' for r in rows):raise ValueError('A passing candidate requires a separate bounded next stage, not a final rejection report')
    source_paths=['research/scenario-study-v2/protocol.json','research/scenario-study-v2/session-correction-v21.json','research/data-audit/full-universe-records.json','research/data-audit/complete-records.json','research/scenario-study-v2/dataset-cache-manifest.json']
    aux={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_paths}
    result={'status':'AGREED_PRICE_SCENARIO_COMPARISON_COMPLETED_WITH_NO_QUALIFYING_CANDIDATE','generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_data_hash':base['source_data_hash'],'auxiliary_input_sha256':aux,'versions':{'original_run':'scenario-v2-run001','original_implementation_commit':'e1cdf71c39aea4be489d375914a7581d2040751a','S2_correction':'s2-session-correction-v21'},'comparison':rows,'canonical_scenario_cells':192,'actually_scored_cells_including_superseded_S2':224,'S3_training_fits':208,'legacy_trial_count':'UNKNOWN','independent_historical_holdout':False,'exact_deployed_agent_return_confirmed':False,'monetary_status':'BLOCKED_DATA','selected_candidate':None,'optimization':'NOT_RUN_NO_QUALIFYING_CANDIDATE','new_seven_day_test':'NOT_STARTED_NO_QUALIFYING_CANDIDATE','existing_virtual_week_and_live_service':'PRESERVED','max_drawdown_user_approved':None,'verification':checks,'unresolved_price_outcomes':0}
    (OUT/'final-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    columns=['strategy','name','version','status','trades','net_R_agent','mean_R_agent','profit_factor','drawdown_R','net_R_human','net_R_stress','net_R_2025','net_R_2026','BR_net_R','GOLD_net_R','net_pnl_rub','net_return_pct','monetary_status']
    for directory in [OUT,DEST]:
        with (directory/'COMPARISON_S0_S5.csv').open('w',newline='',encoding='utf-8-sig') as stream:
            w=csv.DictWriter(stream,fieldnames=columns);w.writeheader()
            for row in rows:
                m=row['agent']['metrics']
                w.writerow({'strategy':row['strategy'],'name':row['name'],'version':row['version'],'status':row['status'],'trades':m['trades'],'net_R_agent':m['net_R'],'mean_R_agent':m['mean_net_R'],'profit_factor':m['profit_factor'],'drawdown_R':m['max_drawdown_R'],'net_R_human':row['human']['metrics']['net_R'],'net_R_stress':row['stress']['metrics']['net_R'],'net_R_2025':row['agent']['years']['2025']['net_R'],'net_R_2026':row['agent']['years']['2026']['net_R'],'BR_net_R':row['agent']['assets']['BR']['net_R'],'GOLD_net_R':row['agent']['assets']['GOLD']['net_R'],'net_pnl_rub':None,'net_return_pct':None,'monetary_status':'BLOCKED_DATA'})
    trade_columns=['strategy','version','asset','contract','signal_begin_msk','signal_end_msk','entry_msk','exit_msk','direction','entry','stop','target','exit','reason','net_R','entry_delay_minutes','timed_exit_lateness_minutes','both_levels_same_minute','source_m1_gap_count']
    with (DEST/'VIRTUAL_SCENARIO_TRADES.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        w=csv.DictWriter(stream,fieldnames=trade_columns);w.writeheader()
        for row in rows:
            for trade in trades[row['strategy']]:
                item={k:trade[k] for k in ['asset','contract','direction','entry','stop','target','exit','reason','net_R','timed_exit_lateness_minutes','both_levels_same_minute','source_m1_gap_count']}
                item.update(strategy=row['strategy'],version=row['version'],signal_begin_msk=timestamp(trade['signal_begin']),signal_end_msk=timestamp(trade['signal_end']),entry_msk=timestamp(trade['entry_time']),exit_msk=timestamp(trade['exit_time']),entry_delay_minutes=trade['entry_time']-trade['signal_end'])
                w.writerow(item)
    with (DEST/'ALL_192_SCENARIOS.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        fields=['strategy','scenario','status','trades','net_R','mean_net_R','profit_factor','max_drawdown_R','unresolved_setups']
        w=csv.DictWriter(stream,fieldnames=fields);w.writeheader()
        for r in cells:w.writerow({'strategy':r['strategy'],'scenario':r['scenario'],'status':r['status'],**{k:r['metrics'][k] for k in fields[3:-1]},'unresolved_setups':r['unresolved_setups']})
    lines=['BR/GOLD: ИТОГ СЦЕНАРНОЙ ПРОВЕРКИ ШЕСТИ СТРАТЕГИЙ','',
        'РЕШЕНИЕ: ни один вариант не прошёл заранее записанный фильтр устойчивости. Кандидат для новой виртуальной недели не выбран. Оптимизация не запускалась. Существующий бот и прежняя виртуальная неделя сохранены.',
        'Это результат согласованного сценарного сравнения. Точная доходность действующего агента, рублёвый результат и просадка капитала остаются неподтверждёнными (BLOCKED_DATA). Прежние +47,51% и +36,67% не подтверждены этой работой.','',
        'ЧТО РАССЧИТАНО','Исходная история: 2023–25.10.2026, включая прогрев H1. Все 16 оставшихся записей восстановлены; 238 записей расширенного набора проверены. Для исполнения использованы только исходные проверенные M1/H1 архивы: 62 непустых окна из 63 замороженных контрактных окон; расширенный набор H1/TradeStats не выдаётся за полный M1.',
        'Обучение временной модели: сначала 2023–2024, затем расширяющееся прошлое. Сравнение: восемь квартальных отрезков 2025–2026, последний неполный; исключены пять дней у обеих границ. Это уже просмотренная история, независимого исторического holdout нет.',
        'Шесть семейств × 32 сценария = 192 случая в итоговой таблице. Всего выполнено 224 расчётных случая с учётом сохранённой прежней версии S2 и её технического исправления. S3: 208 записанных обучений с проверкой конца обучающих меток. Старый полный объём перебора неизвестен.',
        'Общие часы входа: будни основной сессии 10:00–19:00 МСК; фактические наблюдаемые защитные выходы могут быть вне этих часов. Это ограниченный сравнительный контур, отличающийся от круглосуточной проверки условий работающего S0.',
        'Вход: первая наблюдаемая M1 после закрытия H1 + предполагаемая задержка + реакция человека, с округлением времени вперёд до минуты. Исторический BID/OFFER заменён явно раскрытой гипотезой о спреде вокруг цены сделки. Вход по уже прошедшему close не используется.',
        'Базовый агент: задержка 18 минут, спред 2 тика, проскальзывание 2 тика за сторону; фиксированные тарифные сценарии 0,015% брокеру и 0,0132% бирже за сторону. Человек: дополнительно 60 секунд. Стресс: спред 5, проскальзывание 5, комиссии ×2.',
        'Результат измеряется в R: 1R — модельный первоначальный риск сделки до стопа с заданными расходами. Портфель содержит не более одной позиции на актив и двух единиц запланированного риска. R не переводится в рубли или проценты капитала без подтверждённых экономических входов.','',
        'ОСНОВНАЯ ТАБЛИЦА (после заданных расходов)','Вариант | Сделки | net R агента | Средний R | PF | Макс. просадка R | net R человека | net R стресса']
    for row in rows:
        m=row['agent']['metrics'];lines.append(f"{row['strategy']} {row['name']} | {m['trades']} | {fmt(m['net_R'])} | {fmt(m['mean_net_R'],3)} | {fmt(m['profit_factor'],3)} | {fmt(m['max_drawdown_R'])} | {fmt(row['human']['metrics']['net_R'])} | {fmt(row['stress']['metrics']['net_R'])}")
    lines+=['S3: входов нет, поэтому средняя сделка, PF и результат торгов не оцениваются. Это не подтверждённая прибыльность и не нулевая оценка ожидания. Контрольный тест подтверждает, что модель разрешает вход при прогнозе выше порога расходов.','',
        'УСТОЙЧИВОСТЬ ПО ГОДАМ И АКТИВАМ','Вариант | 2025 net R | 2026 net R | BR net R | GOLD net R']
    for row in rows:
        a=row['agent'];lines.append(f"{row['strategy']} | {fmt(a['years']['2025']['net_R'])} | {fmt(a['years']['2026']['net_R'])} | {fmt(a['assets']['BR']['net_R'])} | {fmt(a['assets']['GOLD']['net_R'])}")
    s0=rows[0];s0cells=[r for r in cells if r['strategy']=='S0'];values=[r['metrics']['net_R'] for r in s0cells]
    lines += [f"У S0 в базовом сценарии 2025 год дал {fmt(s0['agent']['years']['2025']['net_R'])}R, 2026 — {fmt(s0['agent']['years']['2026']['net_R'])}R. Его средний R находится в условном 90%-интервале блочной оценки [{fmt(s0['agent']['bootstrap']['lower90_mean_R'],3)}; {fmt(s0['agent']['bootstrap']['upper90_mean_R'],3)}], включающем ноль.",
        f"По 32 вариантам исполнения сумма S0 менялась от {fmt(min(values))}R до {fmt(max(values))}R. Отдельный удачный режим задержки после просмотра таблицы не становится подтверждённым преимуществом. Критерий выбора был записан до расчётов.",
        'Блочная оценка использует 20 наблюдаемых московских дат и 2000 повторов. Это условная ретроспективная проверка чувствительности; она не исправляет неизвестный прежний перебор и не даёт глобального доказательства статистической значимости.','',
        'ИСПРАВЛЕНИЕ S2 И ПРОВЕРКИ','Аудит первой версии обнаружил один перенос внутридневного выхода S2 на следующий день. Первый результат сохранён. До пересчёта зарегистрирован выход за минуту до конца обычных непрерывных торгов: 18:49 до 23.03.2026, затем 18:59. Неподтверждённый выход на следующий календарный день блокирует вывод. Исправлены и повторно рассчитаны только 32 случая S2; в итоговом базовом S2 ночных переносов и задержек планового выхода нет.',
        'Для S0/S1/S4 время выхода может сдвигаться до первой реальной наблюдаемой минуты после нерабочего интервала. Это отражено в журнале, а не заполнено выдуманной ценой. Гарантия исполнения в точную секунду не заявляется.',
        'Проверены хеши входов и кода, полный набор расчётных случаев, согласованность сделок/годов/активов, отсутствие пересечений позиций одного актива, входов раньше заданной доступности и обучающих меток из будущего. Условие стоп/цель внутри одной полной минуты обрабатывается со стопом первым; гэп исполняется по худшей наблюдаемой цене.',
        'Контрольные тесты: 15 проверок причинности и исполнения прошли. Во всех итоговых 192 случаях нет неразрешённых ценовых исходов; это не подтверждение исторической исполнимости BID/OFFER.','',
        'ПОЧЕМУ ДЕНЕЖНЫЕ СТОЛБЦЫ ПУСТЫ','В полученном наборе нет исторических исполнимых BID/OFFER и полной подтверждённой экономики контрактов/комиссий. Все 228279 строк TradeStats 2023 имеют неизвестное ГО; пропуски есть и позже. Текущие значения ГО, брокерская надбавка и сегодняшняя экономика не подставлялись задним числом.',
        'Поэтому не представлены достижимые рублёвые результаты, фактические размеры целых позиций, нагрузка на капитал и просадка капитала в процентах. Предусмотренные сценарии риска 0,5%/1%/2% остаются за денежным ограничителем данных. Порог допустимой просадки пользователь не утверждал.',
        'Подробный реестр входов и ограничений: DATA_SUFFICIENCY_RU.txt и data-register-v2.json. Общий статус денежной части: BLOCKED_DATA.','',
        'КОГДА ВОЗНИКАЕТ И ПРИХОДИТ СИГНАЛ','Работающий S0 проверяет завершённую H1. Для свечи 10:00–11:00 МСК логическое условие относится к её закрытию; действующий буфер разрешает обработку этой новой H1 примерно с 11:18. Затем добавляются очередь GitHub, загрузка, расчёт, сохранение журнала и отправка Telegram. Почасовая сводка — отдельный тип сообщения.',
        'Точный срок получения или прочтения человеком не измерен. В сохранённых примерах сводки приняты Telegram через 19 минут 23 секунды и 59 минут 24 секунды после закрытия H1. Эти две сводки не являются распределением задержек LONG/SHORT.',
        'Отдельно сверены архивы измерений закрытий 12:00 и 22:00 МСК 27.10.2026: финальный относительно +30-минутного наблюдения состав свечи впервые наблюдался через 4,15–62,58 секунды. В нескольких рядах после закрытия была одна правка. Максимум в кампании по трём полным окнам той же даты — около 62,58 секунды.',
        'Это малая выборка: заданный минимум 12 полных окон на актив и три разные даты ещё не выполнен. Четыре минуты — только предварительный кандидат для дальнейших измерений, не гарантированный SLA и не внесённая правка. 18 минут не доказаны как свойство AlgoPack.',
        'Сценарии задержки 1/5 минут — контрфактические проверки, а не обещание доступности. Быстрая доставка полезна только после готовности данных. GitHub Actions не гарантирует точный старт по cron; минимальная альтернатива для проверки — короткие опросы готовности внутри одного запуска после закрытия, без собственного постоянного сервера. Платная инфраструктура не подключалась.','',
        'ЧТО ДЕЛАТЬ ДАЛЬШЕ','Оснований выбрать стратегию и начать новую виртуальную неделю по этим результатам нет. Оптимизация после неудачного фильтра не запускалась; новые семейства не добавлялись. Исходный работающий бот и эксперимент 1d88347c8df1a0db74d49667 сохранены без переключения.',
        'Точная денежная проверка требует подтверждённых экономических входов и наблюдаемого исполнения. Идущая неделя может дать новые операционные наблюдения, но сама по себе не доказывает устойчивую доходность. Любой следующий тест должен иметь отдельную версию, идентификатор, начало и конец после фиксации выбранного кандидата.',
        'Реальные заявки, брокерский доступ и реальные деньги не использовались.','',
        'ВОСПРОИЗВЕДЕНИЕ','Код, протокол, паспорта и журнал перебора: research/scenario-study-v2/ и research/six-strategy-v1/trial-ledger.jsonl. Исходный запуск scenario-v2-run001 и S2 correction-v21 сохранены раздельно. Workflow ручного воспроизведения получает проверенные архивы через GitHub и не обращается к MOEX или Telegram.',
        'Основные источники: https://www.bollingerbands.com/bollinger-band-rules ; https://technical.traders.com/archive/archivelogin.asp?file=%5CV06%5CC09%5CPLAYING.pdf&src=SC ; https://www.kellogg.northwestern.edu/academics-research/research/detail/2010/intraday-patterns-in-the-cross-section-of-stock-returns/ ; https://www.optimalmomentum.com/dual-relative-absolute-momentum/ ; https://help.chaikinanalytics.com/knowledge-center/chaikin-money-flow . Это источники подходов; параметры BR/GOLD являются записанными адаптациями, не доказанной авторами доходностью этих вариантов.',
        'Расписание: https://www.moex.com/n49786 ; https://www.moex.com/n98363 ; https://www.moex.com/n101980 . Полный исторический календарь всех сбоев не заявляется.']
    text='\n'.join(lines)+'\n'
    (OUT/'FINAL_REPORT_RU.txt').write_text(text)
    (DEST/'FINAL_REPORT_RU.txt').write_text(text)
    for src in ['final-results.json','protocol.json','session-correction-v21.json','timing-evidence.json']:
        shutil.copy2(OUT/src,DEST/src)
    for i in range(6):
        passport=json.loads((OUT/f'S{i}-passport.json').read_text())
        content=f"S{i}: {NAMES[f'S{i}']}\nСтатус: сценарная исследовательская спецификация; реальное исполнение не подтверждено.\n\n"+json.dumps(passport,ensure_ascii=False,indent=2)+'\n'
        if i==2:content+='\nИтоговая поправка выхода: session-correction-v21.json имеет приоритет над первоначальным правилом19:00.\n'
        (DEST/f'S{i}_PASSPORT.txt').write_text(content)
    (OUT/'final-primary-trades.json').write_text(json.dumps(trades,allow_nan=False)+'\n')
    (OUT/'final-primary-curves.json').write_text(json.dumps(curves,allow_nan=False)+'\n')
    (OUT/'verification.json').write_text(json.dumps({'status':'PASSED','canonical_cells':192,'actual_score_cells':224,'training_fits':208,'checks':checks,'source_hash':base['source_data_hash'],'economic_gate':'BLOCKED_DATA','original_result_sha256':hashlib.sha256((OUT/'results/scenario-v2-run001/results.json').read_bytes()).hexdigest(),'corrected_S2_result_sha256':hashlib.sha256((OUT/'results/s2-session-correction-v21/results.json').read_bytes()).hexdigest()},indent=2)+'\n')
    print(json.dumps({'result':str(OUT/'final-results.json'),'selected_candidate':None,'canonical_cells':192,'scored_cells':224,'report':str(DEST/'FINAL_REPORT_RU.txt')}))


if __name__=='__main__':build()
