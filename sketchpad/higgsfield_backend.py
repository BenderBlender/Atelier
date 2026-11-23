"""Official bundled Higgsfield CLI transport. Credentials stay in its OAuth store."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from urllib.parse import urlparse
from .assistant_backend import ROOT, MAX_IMAGE, AssistantError

DEFAULT_MODEL = 'seedream_5_5'
# Verified against the live official CLI catalog; MCP has a different alias here.
CLI_IDS = {'nano_banana_2': 'nano_banana_flash'}


def catalog():
    return json.loads((ROOT/'presets'/'higgsfield-models.json').read_text(encoding='utf8'))['items']


def model_info(identifier):
    return next((m for m in catalog() if m['id'] == identifier), None)


def run(args, cancel, timeout=90, raw=False):
    executable = ROOT/'runtime'/'hf.exe'
    if not executable.is_file():
        raise AssistantError('Не найден встроенный компонент Higgsfield: runtime/hf.exe.')
    cancel.check()
    process = subprocess.Popen([str(executable), *map(str, args), '--json', '--no-color'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), shell=False)
    started = time.monotonic()
    try:
        while True:
            cancel.check()
            try:
                output, error = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic()-started > timeout:
                    raise AssistantError('Higgsfield не ответил вовремя. Автоматического повтора нет; проверьте историю заданий перед новым запуском.')
        if process.returncode:
            message = error.decode('utf8', errors='replace')
            if any(s in message.lower() for s in ('auth', 'token', 'login', 'session', 'workspace')):
                raise AssistantError('Higgsfield: откройте «Подключение», войдите в аккаунт и выберите рабочее пространство. Вход в MCP чата не переносится в Atelier автоматически.')
            raise AssistantError('Higgsfield отклонил запрос. Проверьте доступность модели, настройки и баланс. Повторного запроса не было.')
        if len(output) > 8*1024*1024:
            raise AssistantError('Higgsfield вернул слишком большой ответ.')
        if raw:
            return output.decode('utf8', errors='replace')
        try:
            return json.loads(output)
        except ValueError as exc:
            raise AssistantError('Higgsfield вернул неподдерживаемый ответ. Повторной генерации нет.') from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def workspaces(cancel, login=False):
    if login:
        # Do not return login output: it may contain a transient OAuth URL.
        run(['auth', 'login'], cancel, timeout=300, raw=True)
    value = run(['workspace', 'list'], cancel)
    items = value if isinstance(value, list) else value.get('workspaces', value.get('items', []))
    if not isinstance(items, list) or not items:
        raise AssistantError('Higgsfield не вернул рабочие пространства аккаунта.')
    return {'workspaces': [{'id': item['id'], 'name': item.get('name') or 'Личное пространство'}
                           for item in items if isinstance(item, dict) and isinstance(item.get('id'), str)],
            'message': 'Higgsfield подключён. Выберите рабочее пространство.'}


def select_workspace(identifier, cancel):
    if not re.fullmatch(r'[a-fA-F0-9-]{32,36}', identifier):
        raise AssistantError('Выберите рабочее пространство Higgsfield в настройках подключения.')
    return run(['workspace', 'set', identifier], cancel, raw=True)


def parameters(identifier, aspect, values, width, height):
    model = model_info(identifier)
    if not model:
        raise AssistantError('Неизвестная модель Higgsfield.')
    ratios = model['aspect_ratios']
    if aspect == 'auto' and 'auto' not in ratios:
        numeric = [r for r in ratios if re.fullmatch(r'\d+:\d+', r)]
        if not numeric:
            raise AssistantError('Эта модель не поддерживает выбор пропорций.')
        aspect = min(numeric, key=lambda r: abs(math.log((int(r.split(':')[0])/int(r.split(':')[1]))/(width/height))))
    if aspect not in ratios:
        raise AssistantError('Эти пропорции недоступны выбранной модели.')
    result = {'aspect_ratio': aspect}
    available = {p['name']: p for p in model['parameters']}
    for key, value in values.items():
        spec = available.get(key)
        if not spec or value not in spec.get('options', []):
            raise AssistantError('Недопустимая настройка Higgsfield: '+key)
        result[key] = value
    return result


def job_ids(value):
    if isinstance(value, list):
        return [identifier for item in value for identifier in job_ids(item)]
    if not isinstance(value, dict):
        return []
    jobs = value.get('jobs', value.get('job_ids', []))
    if not jobs and isinstance(value.get('data'), dict):
        return job_ids(value['data'])
    if not jobs and isinstance(value.get('data'), list):
        return job_ids(value['data'])
    if not jobs and ('job_id' in value or 'id' in value):
        jobs = [value.get('job_id', value.get('id'))]
    return [i for job in jobs if isinstance(i := (job.get('id', job.get('job_id')) if isinstance(job, dict) else job), str)
            and re.fullmatch(r'[a-fA-F0-9-]{32,36}', i)]


def output_url(value):
    if not isinstance(value, dict):
        return None
    status = value.get('status')
    if status in ('failed', 'error', 'canceled', 'cancelled'):
        raise AssistantError('Higgsfield не завершил генерацию. Проверьте задание в аккаунте; повтор автоматически не запускался.')
    if status in ('completed', 'succeeded', 'done'):
        for key in ('result_url', 'url', 'image_url'):
            if isinstance(value.get(key), str):
                return value[key]
        result = value.get('result', value.get('results', value.get('output')))
        if isinstance(result, list):
            result = result[0] if result else None
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get('url') or result.get('image_url')
    for key in ('job', 'data'):
        found = output_url(value.get(key))
        if found:
            return found
    return None


def download(url, cancel):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        raise AssistantError('Некорректная ссылка на результат Higgsfield.')
    cancel.check()
    with urllib.request.urlopen(url, timeout=60) as response:
        chunks, total = [], 0
        while chunk := response.read(65536):
            cancel.check()
            total += len(chunk)
            if total > MAX_IMAGE:
                raise AssistantError('Результат Higgsfield превышает допустимый размер.')
            chunks.append(chunk)
    return b''.join(chunks)


def retrieve(directory, cancel, progress=lambda text: None):
    path = Path(directory)/'higgsfield-job.json'
    record = json.loads(path.read_text(encoding='utf8'))
    ids = job_ids(record)
    if len(ids) != 1:
        raise AssistantError('Не найден идентификатор задания. Проверьте историю Higgsfield.')
    progress('Higgsfield создаёт изображение…')
    started = time.monotonic()
    while time.monotonic()-started < 900:
        cancel.check()
        result = run(['generate', 'get', ids[0]], cancel)
        url = output_url(result)
        if url:
            image = download(url, cancel)
            output = Path(directory)/'result.png'
            output.write_bytes(image)
            return {'path': str(output), 'provider': 'higgsfield', 'job_id': ids[0]}
        if cancel.event.wait(5):
            cancel.check()
    raise AssistantError('Задание Higgsfield ещё не готово. Нажмите «Получить результат» позже — это не запускает новую генерацию.')


def generate(request, cancel, progress):
    settings = request.provider_options
    identifier = settings.get('model', DEFAULT_MODEL)
    width, height = settings.get('source_size', [1, 1])
    params = parameters(identifier, settings.get('aspect', 'auto'), settings.get('parameters', {}), width, height)
    if request.mask and params['aspect_ratio'] != 'auto':
        a, b = map(int, params['aspect_ratio'].split(':'))
        if abs((a/b)/(width/height)-1) > .025:
            raise AssistantError('Эти пропорции изменят положение выделения. Для Inpaint выберите модель с Auto (например, Seedream 5.5). Генерация не отправлена.')
    select_workspace(settings.get('workspace', ''), cancel)
    cli_model = CLI_IDS.get(identifier, identifier)
    # Check actual CLI availability before uploading or creating a paid job.
    live = run(['model', 'get', cli_model], cancel)
    specs = {p['name']: p for p in live.get('params', [])}
    for name, value in params.items():
        if name not in specs or value not in specs[name].get('enum', []):
            raise AssistantError('Параметры модели Higgsfield изменились. Выберите другую настройку; запрос генерации не отправлен.')
    prompt = request.prompt
    if request.enhance:
        from .agent_backend import refine_prompt
        progress('Уточняю описание…')
        prompt = refine_prompt(request, cancel)
    if request.context:
        prompt = request.context+'\n\nUSER REQUEST:\n'+prompt
    args = ['generate', 'create', cli_model, '--prompt', prompt, '--image', request.source]
    if request.mask:
        if not request.guide or not request.guide.is_file():
            raise AssistantError('Для локальной правки нужна схема выделения.')
        args += ['--image', request.guide]
    for key, value in params.items():
        args += ['--'+key, str(value)]
    request.directory.mkdir(parents=True, exist_ok=True)
    (request.directory/'higgsfield-request.json').write_text(json.dumps({'model': identifier, 'cli_model': cli_model,
        'prompt': prompt, 'parameters': params}, ensure_ascii=False, indent=2), encoding='utf8')
    progress('Отправляю скетч в Higgsfield…')
    result = run(args, cancel, timeout=180)
    ids = job_ids(result)
    (request.directory/'higgsfield-job.json').write_text(json.dumps({'job_ids': ids}), encoding='utf8')
    if len(ids) != 1:
        raise AssistantError('Higgsfield не вернул один идентификатор. Не повторяйте запрос до проверки истории заданий.')
    return retrieve(request.directory, cancel, progress)
