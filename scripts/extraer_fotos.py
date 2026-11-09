#!/usr/bin/env python3
"""Extrae imágenes destacadas verificables de los seis artículos y actualiza el index.
Se ejecuta en GitHub Actions, donde hay acceso de red a eltribuno.com.
"""
import json, re, sys, time, html as htmlmod
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'index.html'
FOTOS = ROOT / 'imagenes'
FOTOS.mkdir(exist_ok=True)

URLS = [
 'https://www.eltribuno.com/salta/2026-11-4-11-52-0-a-39-anos-de-la-visita-de-juan-pablo-ii-a-salta-las-imagenes-historicas-que-guarda-el-tribuno',
 'https://www.eltribuno.com/salta/2026-11-9-0-22-0-como-se-vivio-la-espera-de-los-saltenos-por-juan-pablo-ii',
 'https://www.eltribuno.com/salta/2026-11-6-9-15-0-delia-aguilar-historica-locutora-de-radio-salta-recordo-la-visita-de-juan-pablo-ii-me-abrazo-toda-la-vida',
 'https://www.eltribuno.com/salta/2026-11-9-0-37-0-asi-se-movilizaron-los-fieles-para-el-encuentro',
 'https://www.eltribuno.com/salta/2026-11-6-18-38-0-a-horas-de-la-llegada-de-leon-xiv-una-placa-recuerda-el-paso-de-juan-pablo-ii-por-salta',
 'https://www.eltribuno.com/salta/2026-11-6-8-24-0-la-noche-en-que-salta-cuido-el-sueno-de-juan-pablo-ii-los-recuerdos-de-monsenor-bernacki',
]
SESSION=requests.Session()
SESSION.headers.update({'User-Agent':'Mozilla/5.0 (compatible; ArchivoPapal/1.0; +https://www.eltribuno.com/)', 'Accept-Language':'es-AR,es;q=0.9'})

def image_candidates(soup, base):
    c=[]
    for attrs in ({'property':'og:image:secure_url'},{'property':'og:image'},{'name':'twitter:image'},{'property':'twitter:image'},{'itemprop':'image'}):
        for meta in soup.find_all('meta',attrs=attrs):
            val=meta.get('content')
            if val: c.append(urljoin(base,val))
    for element in soup.select('script[type="application/ld+json"]'):
        try: data=json.loads(element.string or element.get_text())
        except (ValueError,TypeError): continue
        def scan(node):
            if isinstance(node,list):
                for x in node: yield from scan(x)
            elif isinstance(node,dict):
                typ=node.get('@type','')
                if (isinstance(typ,str) and ('Article' in typ or typ=='NewsArticle')) or (isinstance(typ,list) and any('Article' in x for x in typ if isinstance(x,str))):
                    img=node.get('image')
                    for src in ([img] if isinstance(img,str) else img if isinstance(img,list) else [img] if isinstance(img,dict) else []):
                        if isinstance(src,str): yield urljoin(base,src)
                        elif isinstance(src,dict) and src.get('url'): yield urljoin(base,src['url'])
                for value in node.values(): yield from scan(value)
        c.extend(scan(data))
    # Fallback: solo imágenes dentro del contenido de la noticia, sin logos del sitio.
    for img in soup.select('article img, main img, .nota img, .article img'):
        src=img.get('data-src') or img.get('src') or img.get('data-original')
        if not src:
            srcset=img.get('data-srcset') or img.get('srcset','')
            if srcset: src=srcset.split(',')[-1].strip().split(' ')[0]
        if src: c.append(urljoin(base,src))
    # La URL debe ser una imagen HTTP válida, no un data-uri ni imagen de estructura.
    seen=set()
    for u in c:
        if not u.startswith(('https://','http://')) or u in seen: continue
        seen.add(u)
        if re.search(r'(logo|avatar|icon|sprite|placeholder|favicon)',urlparse(u).path,re.I): continue
        yield u

def download_image(url, pathbase):
    r=SESSION.get(url,timeout=25,headers={'Referer':'https://www.eltribuno.com/'})
    r.raise_for_status()
    typ=r.headers.get('Content-Type','').lower()
    if not typ.startswith('image/') or 'svg' in typ: raise ValueError('Respuesta no es foto raster: '+typ)
    if len(r.content) < 2500: raise ValueError('Imagen demasiado pequeña')
    from PIL import Image
    from io import BytesIO
    im=Image.open(BytesIO(r.content))
    if im.width < 250 or im.height < 140: raise ValueError('Dimensiones demasiado pequeñas')
    if im.mode not in ('RGB','L'): im=im.convert('RGB')
    if im.mode=='L': im=im.convert('RGB')
    target=pathbase.with_suffix('.webp')
    im.save(target,'WEBP',quality=84,method=5)
    return target

def main():
    source=INDEX.read_text(encoding='utf-8')
    articles=re.findall(r'<article class="archivo-papal__card">.*?</article>',source,flags=re.S)
    if len(articles)!=6: raise RuntimeError(f'Se esperaban 6 noticias, encontradas {len(articles)}')
    count=0
    for i,url in enumerate(URLS):
        print(f'[{i+1}/6] {url}',flush=True)
        try:
            r=SESSION.get(url,timeout=25); r.raise_for_status()
            soup=BeautifulSoup(r.text,'html.parser')
            image_path=None
            for candidate in image_candidates(soup,r.url):
                try:
                    image_path=download_image(candidate,FOTOS/f'nota_{i+1}')
                    print('  Imagen:',candidate,'->',image_path.relative_to(ROOT),flush=True)
                    break
                except Exception as exc:
                    print('  Candidato descartado:',str(exc)[:120],flush=True)
            if not image_path:
                print('  SIN IMAGEN VERIFICADA: se conserva el marcador y no se inventa foto',flush=True)
                continue
            relative=str(image_path.relative_to(ROOT))
            photo=f'<img src="{relative}" alt="Fotografía de la nota {i+1} de El Tribuno" loading="lazy" decoding="async" />'
            new_article=re.sub(r'(<div class="archivo-papal__thumb">)\s*<div class="archivo-papal__fallback">.*?</div>',lambda m:m.group(1)+'\n                '+photo,articles[i],count=1,flags=re.S)
            if new_article==articles[i]: print('  Aviso: no se encontró el placeholder esperado',flush=True); continue
            source=source.replace(articles[i],new_article,1)
            count+=1
        except Exception as exc:
            print('  ERROR:',exc,flush=True)
    INDEX.write_text(source,encoding='utf-8')
    print(f'Fotos verificadas e incorporadas: {count}/6',flush=True)
    # Salida distinta de cero si faltan fotos, para no dar una impresión falsa de éxito.
    return 0 if count==6 else 2

if __name__=='__main__': sys.exit(main())
