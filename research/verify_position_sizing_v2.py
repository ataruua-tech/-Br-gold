"""Independent common-contract sizing guard; never invents missing economic inputs."""
import math


def position_quantity(*,equity_rub,stop_risk_one_rub,margin_one_rub,median_volume,
                      risk_fraction,used_margin_rub=0,used_stop_risk_rub=0,economics_confirmed=False):
    if not economics_confirmed:
        return {'status':'BLOCKED_ECONOMICS','quantity':None,'reason':'Historical point value/margin/economics not fully attested; no fallback'}
    values=[equity_rub,stop_risk_one_rub,margin_one_rub,median_volume,risk_fraction,used_margin_rub,used_stop_risk_rub]
    if any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in values) or min(stop_risk_one_rub,margin_one_rub,median_volume,risk_fraction)<=0 or min(used_margin_rub,used_stop_risk_rub)<0:
        return {'status':'DATA_ERROR','quantity':None,'reason':'Invalid or missing confirmed sizing inputs'}
    risk_budget=max(0,min(equity_rub*risk_fraction,equity_rub*.02-used_stop_risk_rub))
    margin_budget=max(0,equity_rub*.5-used_margin_rub)
    limits={'individual_and_combined_stop_risk':math.floor(risk_budget/stop_risk_one_rub),
            'combined_margin':math.floor(margin_budget/margin_one_rub),
            'known_volume':math.floor(median_volume*.01)}
    quantity=max(0,min(limits.values()))
    return {'status':'SIZED' if quantity else 'SKIPPED_CAPITAL_OR_VOLUME','quantity':quantity,'limits':limits,'risk_rub':quantity*stop_risk_one_rub,'margin_rub':quantity*margin_one_rub,'scope':'Shared research sizing convention, not a change to the frozen live engine'}
