"""Restore verified original candle ZIPs for offline reproduction; no MOEX calls."""
import hashlib
import io
import zipfile
from pathlib import Path
from audit_required_input_artifacts import github_get

ROOT=Path(__file__).resolve().parents[1]
PRICE_ARCHIVES={2023:(12731587244,'fdbd8f024c12ab110c0d9f15a1aabd107039bde066b0410054a9b7939abf578c'),2024:(12733895222,'5a99d6eee7f98d7e76a1dffe86dcfeea4b523be14c587b22f8c3000905d4c04c'),2025:(12734831380,'d8eff7373c9bcaafb570fbd24a44d84709353112396f90c62bd299e86b003253'),2026:(12735884135,'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb')}


def restore():
    for year,(artifact,sha) in PRICE_ARCHIVES.items():
        directory=ROOT/'research/point-in-time'/str(year);directory.mkdir(parents=True,exist_ok=True)
        existing=directory/'download.zip'
        if existing.exists() and hashlib.sha256(existing.read_bytes()).hexdigest()==sha:
            raw=existing.read_bytes()
        else:
            raw=github_get('/repos/ataruua-tech/-Br-gold/actions/artifacts/'+str(artifact)+'/zip',archive=True)
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Original ZIP digest mismatch')
            existing.write_bytes(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if z.testzip() is not None:raise ValueError('ZIP CRC mismatch')
            for item in z.infolist():
                if Path(item.filename).is_absolute() or '..' in Path(item.filename).parts or ((item.external_attr>>16)&0o170000)==0o120000:
                    raise ValueError('Unsafe archive member')
            z.extractall(directory)
        print('Verified/restored',year,sha,flush=True)


if __name__=='__main__':restore()
