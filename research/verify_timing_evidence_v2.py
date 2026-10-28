"""Read and summarize already collected latency archives; no live API or messages."""
import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path
from audit_required_input_artifacts import github_get, emit_record

TIMING_ARTIFACTS = [
    (40476601927,12722438767,'9d39f83c32427e119439804b6320fc5d675869d62d684acda7f2f14a33ebd30a'),
    (40558750415,12764821760,'8499c016dadff4ef0e25e6046cbe6e264195ba53c427b6f98650703a649b1ffd'),
]


def timing_main(directory=Path('timing-evidence')):
    directory.mkdir(exist_ok=True)
    result={'mode':'READ_ONLY_ARCHIVE_ANALYSIS','market_requests':0,'telegram_messages':0,'live_code_changed':False,'reports':[]}
    for run_id,artifact_id,sha in TIMING_ARTIFACTS:
        raw=github_get('/repos/ataruua-tech/-Br-gold/actions/artifacts/'+str(artifact_id)+'/zip',archive=True)
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Timing ZIP digest mismatch')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if z.testzip() is not None:raise ValueError('Timing ZIP CRC mismatch')
            report=json.loads(z.read('latency-report.json'))
        if report.get('source')!='apim.moex.com' or report.get('orders_enabled') is not False or report.get('production_buffer_changed') is not False:
            raise ValueError('Unexpected timing source or live change')
        item={'run_id':run_id,'artifact_id':artifact_id,'sha256':sha,'status':report['status'],'clock_basis':report.get('clock_basis'),'campaign_review':report.get('campaign_review'),'windows':[]}
        for window in report['windows']:
            item['windows'].append({'close_msk':window['target_close_msk'],'analysis':window['analysis']})
        result['reports'].append(item)
    (directory/'timing-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    emit_record({'type':'VERIFIED_TIMING_EVIDENCE',**result},'timing-final')


if __name__=='__main__':timing_main()
