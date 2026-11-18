[English](README.md) | Türkçe

# beatcut

beatcut, bir müzik parçasının vuruşlarına göre kısa bir video keser. Klip klasörünü tarayıcıda açılan yerel bir seçici sayfasında gezersiniz; her çekim için giriş noktasını, çıkış noktasını ve vuruş sayısını seçersiniz. beatcut seçimleri `shots.json` dosyasına yazar ve videoyu ffmpeg ile render eder. Her çekim çıktının tam bir karesinde başlar ve biter; böylece görüntü müzikle senkron kalır.

Bir Claude Code skill'idir (bkz. `SKILL.md`) ve aynı zamanda sade bir komut satırı aracıdır: aşağıdakilerin hepsi Claude olmadan da çalışır.

![beatcut demo](docs/demo.gif)

## Gereksinimler

- Python 3.10 veya üstü
- `PATH` üzerinde `ffmpeg` ve `ffprobe` (bu depoyla gelmez)
- İsteğe bağlı: NVIDIA ekran kartı. `encoder` değeri `auto` (varsayılan) iken, ffmpeg NVENC'i listeliyorsa ve tek karelik bir deneme kodlaması başarılıysa NVENC kullanılır; yoksa işlemci kullanılır.

## Kurulum

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` içinde `librosa`, `numpy` ve `soundfile` vardır; vuruş tespiti (`beats`) için gerekir.

### Claude Code skill'i olarak kurma

Depoyu kişisel skill klasörünüze, `~/.claude/skills/beatcut` (Windows'ta `%USERPROFILE%\.claude\skills\beatcut`) ya da bir projenin `.claude/skills/beatcut` klasörüne klonlayın:

```
git clone https://github.com/faruk76150/beatcut.git ~/.claude/skills/beatcut
```

Sonra o klasörde, yukarıda gösterildiği gibi sanal ortamı oluşturun ve gereksinimleri kurun. Claude Code skill'i `SKILL.md` üzerinden bulur; `SKILL.md` ona `beatcut.py` dosyasını skill'in kurulu olduğu klasörden çalıştırmasını söyler.

## Demoyu çalıştırma

```
python beatcut.py check examples/demo/beatcut
python beatcut.py draft examples/demo/beatcut
```

Taslak `examples/demo/beatcut/out/demo_draft.mp4` dosyasına yazılır.

## Kendi kliplerinizle başlama

Komutları depo klasöründen çalıştırın. `init` ve `sheet` dışındaki her komut bir proje klasörü alır (`init`'in oluşturduğu `beatcut` klasörü).

| Komut | Ne yapar | Çıkış kodları |
|---|---|---|
| `python beatcut.py init <klip klasörü> --music <dosya>` | `<klip klasörü>/beatcut/` klasörünü oluşturur, müzik dosyasını içine kopyalar, `project.json` (bütün varsayılanlar yazılı) ve boş bir `shots.json` yazar. `created: <klasör>` yazdırır. `project.json` zaten varsa hiçbir şeyi değiştirmeden durur. | 0 tamam; 1 klip klasörü ya da müzik yok, ya da proje zaten var |
| `python beatcut.py beats <proje>` | Müziği inceler, `period` ve `t0` değerlerini `shots.json` dosyasına yazar (diğer alanlar korunur; eski dosya `shots.json.bak` olarak saklanır). `period`, `t0` ve `bpm` yazdırır. `librosa` gerekir. | 0 tamam; 1 müzik dosyası yok ya da dörtten az vuruş bulundu; 2 vuruş tespiti kullanılamıyor |
| `python beatcut.py pick <proje> [--port N] [--no-open]` | Seçiciyi başlatır (varsayılan port 8765), `beatcut picker: http://127.0.0.1:<port>/  (Ctrl+C to stop)` yazar, `--no-open` verilmediyse tarayıcıyı açar. Ctrl+C ile durdurulur. | Ctrl+C sonrası 0 |
| `python beatcut.py check <proje>` | `project.json`, `shots.json`, klipler ve planı doğrular. Uyarılar ve hatalar stderr'e, satır satır yazılır. Hata yoksa `ok: <n> shots, <s> s` yazdırır. | 0 tamam (uyarı olabilir); 1 girdide sorun; 2 ffmpeg yok ya da başka çalışma hatası |
| `python beatcut.py draft <proje>` | Aynı denetimi yapar, sonra `draft` boyutunda `out/<out_name>_draft.mp4` render eder. `encoder: <cpu ya da nvenc>`, `segment i/n` satırlarını ve çıktı yolunu yazdırır. | `check` gibi; ayrıca projede başka bir render sürüyorsa 2 |
| `python beatcut.py final <proje>` | Aynısı, `final` boyutunda, `out/<out_name>_final.mp4` dosyasına. | `draft` gibi |
| `python beatcut.py sheet <video> [--out dosya.jpg]` | 5x4'lük bir kare özeti (eşit aralıklı 20 kare) JPEG olarak yazar. Varsayılan: geçerli klasörde `<video adı>_sheet.jpg`. Yolu yazdırır. | 0 tamam; 1 dosya yok; 2 ffmpeg sorunu |

Olağan akış: `init`, `beats`, `pick`, `check`, `draft`, sonuca bakma (`sheet`), sonra `final`. Proje her zaman `<klip klasörü>/beatcut` olur; komutlara klip klasörünü değil bu klasörü verin.

`draft` ve `final`, `check` hata verecekse başlamaz. Taslak çıktısı H.264, son çıktı H.265'tir (`hvc1`; işlemci ya da NVENC), ses AAC'dir. İkisi de BT.709 olarak etiketlenir. Video ilk 30 karede siyahtan açılır ve son `tail` saniyede kararır.

### Proje klasörü

```
<klip klasörü>/
  klip1.mp4 ...        kaynak klipleriniz (hiç değiştirilmez)
  beatcut/
    project.json       ayarlar
    shots.json         kurgu
    <müzik dosyası>    init kopyalar
    shots.json.bak     önceki sürüm, her kayıtta alınır
    proxy/             seçicinin ürettiği küçük önizleme kopyaları
    work/              ara segmentler ve önbellekler
    out/               bitmiş videolar
```

Klipler yalnızca `clips_dir` klasörünün kendisinden okunur (alt klasörler taranmaz).

### project.json

| Alan | Anlamı | Varsayılan |
|---|---|---|
| `clips_dir` | Klip klasörü, proje klasörüne göreli | `..` |
| `extensions` | Taranacak uzantılar, büyük küçük harf fark etmez | `[".mp4", ".mov", ".mxf"]` |
| `exclude_prefix` | Adı bu öneklerden biriyle başlayan dosyalar atlanır | `[]` |
| `music` | Müzik dosyası, proje klasörüne göreli (mutlak yol ve `..` olmaz) | zorunlu |
| `fps` | Çıktı kare hızı, `"25"` ya da `"30000/1001"` biçiminde | `"30000/1001"` |
| `draft` | Taslak boyutu; genişlik ve yükseklik, ikisi de pozitif çift tam sayı | `{"width": 1920, "height": 1080}` |
| `final` | Son boyut, aynı kurallar | `{"width": 3840, "height": 2160}` |
| `fill` | Kaynağın biçimi çıktıdan farklıysa: `cover` ya da `blur` | `cover` |
| `encoder` | `auto`, `cpu` ya da `nvenc` (`nvenc` yoksa hata verir) | `auto` |
| `loudness` | Hedef ses yüksekliği, LUFS, -40 ile -5 arası | `-15` |
| `profiles` | Ek renk profilleri: ad ve ffmpeg filtre zinciri | `{}` |
| `out_name` | Çıktı dosya adının başı | klip klasörünün adı (`<klipler>/beatcut` projesinde), değilse proje klasörünün adı |

Bilinmeyen anahtar hatadır. `init`, `out_name` dışındaki bütün alanları yazar.

### shots.json

| Alan | Anlamı | Varsayılan |
|---|---|---|
| `version` | `1` olmalı | zorunlu |
| `period` | Bir vuruşun süresi (sn), 0,05'ten büyük 10'dan küçük | zorunlu |
| `t0` | Müzikteki ilk vuruşun saniyesi, 0 ya da büyük | zorunlu |
| `tail` | Son vuruştan sonra eklenen süre (sn), 0 ile 30 arası. Son çekim bu kadar uzatılır ve görüntü bu sürede kararır | `1.5` |
| `shots` | Sıralı liste, en çok 2000 | zorunlu |
| `shots[].clip` | `clips_dir` içindeki dosya adı, uzantısıyla; yol ayracı ve `:` olmaz | zorunlu |
| `shots[].in`, `shots[].out` | Kaynak klipteki saniye; `in` 0 ya da büyük, `out` `in`'den büyük | zorunlu |
| `shots[].beats` | 1 ile 512 arası tam sayı | zorunlu |
| `shots[].fit` | `fit` (vuruşları doldurmak için hızı değiştirir) ya da `cut` (1x oynar, vuruş sayısında keser) | `fit` |
| `shots[].interp` | Yavaş çekim kareleri: `oflow`, `blend`, `none`; yazılmazsa otomatik | otomatik |
| `shots[].prof` | Renk profili adı | `neutral` |
| `shots[].tin` | Çekime giriş geçişi: `cut`, `fade`, `fadewhite` | `cut` |
| `shots[].tdur` | Geçiş süresi, saniye, 0 ile 5 arası | `0.5` |
| `shots[].note` | Serbest not, en çok 2000 karakter; render'ı etkilemez | `""` |
| `bad` | Klip adı ve kullanmak istemediğiniz `{"a", "b", "note"}` aralıklarının listesi (saniye); bir çekim bunlarla çakışırsa `check` uyarır | `{}` |
| `clipnotes` | Klip adı ve not | `{}` |
| `markers` | Seçici zaman çizelgesinde görünen `{"beat", "title"}` etiketleri; render'ı etkilemez | `[]` |

Seçici ya da `beats` dosyayı kaydederken programın tanımadığı anahtarlar korunur.

### Hız ve kare hassasiyeti

`fit` kipinde hız `(out - in) / (beats * period)` formülüyle bulunur; 1'e 0,015'ten yakın hız tam 1 sayılır ve `check` 0,1x ile 8x dışındaki hızları reddeder. Her vuruş sınırı çıktının tam bir karesine yuvarlanır ve her segment tam kare sayısıyla render edilir; böylece uzun bir kurguda bile görüntü müzikten kaymaz.

İlk çekim videonun ilk karesinde başlar, yani ilk vuruştan önceki süreyi de kapsar; bunu doldurmak için `out` noktasının ötesini okur. Klip buna yetmiyorsa `check` söyler.

### Geçişler

Varsayılan `cut`'tır. `fade` çapraz geçiş, `fadewhite` beyaza parlama geçişidir; ikisi de vuruş sınırının iki yanına eşit paylaştırılır ve `tdur` çift sayıda kareye yuvarlanır (2 kareden kısa kalan süre kesmeye döner). Geçiş bir çekimden uzunsa hata verilir.

### Yavaş çekim

Bir `fit` çekimi 0,97x'ten yavaş çalışıyorsa ve kaynağın kare hızı ile hızın çarpımı çıktı kare hızının altında kalıyorsa, beatcut ara kareler üretir: taslakta `blend`, son render'da `oflow` (optik akış). Aksi hâlde üretmez. Bunu çekim başına `interp` ile değiştirebilirsiniz. `cut` çekimleri ve hızlanan çekimler hiç ara kare üretmez.

### Renk profilleri

Yerleşik olanlar: `neutral` (işlem yok), `warm`, `cool`, `contrast`, `golden`. Kendi profilinizi `project.json` içine ekleyin:

```json
"profiles": { "teal": "colorbalance=bs=.03:bm=.03,eq=saturation=1.1" }
```

Profil, RGB karelere uygulanan tek bir ffmpeg filtre zinciridir. Adlar `[a-z0-9_-]` karakterlerinden oluşur (1 ile 32 karakter); zincirde `;`, `[` ve `]` olamaz. Yerleşik bir adla eklenen özel profil onun yerine geçer. `check`, kullanılan her profili ffmpeg'e denetletir. Filtreler yalnızca `project.json` içindedir; `shots.json` yalnızca ad taşır, bu yüzden başkasından aldığınız bir `shots.json` filtre çalıştıramaz.

### Yön ve kadraj

Çıktı boyutu `draft` ve `final` değerlerinden gelir; bu yüzden dikey çıktı (ör. 1080x1920) da alınabilir. Kaynağın dönüş (rotation) bilgisi dikkate alınır. Kaynağın biçimi çıktıdan farklıysa `fill` belirler: `cover` kareyi dolduracak şekilde kırpar, `blur` klibi bütün olarak sığdırır ve boşluğu kendisinin bulanık kopyasıyla doldurur. Ayar bütün proje için geçerlidir.

## Seçici

`pick`, `http://127.0.0.1:<port>/` adresinde bir sayfa sunar. Soldan klibi seçin, giriş ve çıkışı kaydırma çubuğunda ya da tuşlarla belirleyin, vuruş sayısını seçin (çipler ortaya çıkan hızı gösterir) ve çekimi ekleyin. Değişiklikler `shots.json` dosyasına kendiliğinden kaydedilir. Sayfada ayrıca vuruş aralığı, ilk vuruş ve kapanış payı düzenlenir; çekimler zaman çizelgesinde sürüklenerek yeniden sıralanır (bir bloğun sağ kenarını sürüklemek vuruş sayısını değiştirir), kötü aralıklar işaretlenir, üst çubuktan `check`, `draft` ve `final` çalıştırılır. Dil sırasıyla adresteki `?lang=en` ya da `?lang=tr` değerinden, son tercihinizden ve tarayıcı dilinden seçilir.

| Tuş | İş |
|---|---|
| Boşluk | Oynat ya da duraklat |
| Sol / Sağ | 0,5 sn geri ya da ileri |
| Shift + Sol / Sağ | 5 sn geri ya da ileri |
| Alt + Sol / Sağ, `,` `.` | Bir kare geri ya da ileri |
| I | Giriş noktasını oynatma konumuna koy |
| O | Çıkış noktasını oynatma konumuna koy |
| A | Çekimi ekle (hiçbir çekim seçili değilken) |
| X | Aralığı kötü olarak işaretle |
| P | Aralığı hesaplanan hızda oynat |
| `[` `]` ya da Yukarı / Aşağı | Önceki ya da sonraki klip |
| Delete | Seçili çekimi sil |
| Esc | Seçimi kaldır (yeni çekim) ya da pencereyi kapat |
| Ctrl + Z | Geri al (80 adım) |
| ? | Kısayol listesini göster ya da gizle |

Önizlemeler `proxy/` içindeki küçük kopyalardır (960 piksel, sessiz); ilk açılışta bütün klipler arka planda dönüştürülür.

## Güvenlik notu

Seçici yalnızca `127.0.0.1` adresini dinler. `Host` ve `Origin` başlıklarını denetler, başka sitelerden gelen istekleri reddeder ve bir şeyi değiştiren her istekte (sayfa sunulurken içine konan) rastgele bir oturum anahtarı ister. Bu, açık olan bir web sayfasının projenizi okumasını ya da render başlatmasını engeller. Aynı bilgisayarda çalışan diğer programlara karşı koruma sağlamaz; onlar proje verisini okuyabilir, sayfayı ve anahtarı alabilir. Sunucuyu dış ağa açmayın (port yönlendirme, tünel ya da ters vekil kullanmayın).

## Sınırlar

- Yalnızca Windows'ta denendi. Linux ve macOS denenmedi.
- HDR kaynaklara ton eşleme yapılmaz. Kullanılan bir klip HDR ise `check` uyarır; renkler soluk görünür. Çıktı her zaman BT.709 olarak etiketlenir; BT.2020 ya da BT.601 matrisli kaynaklar BT.709'a çevrilir.
- Çok uzun kurgular tek geçişte birleştirilemez. `check` bunu önceden `too many shots to assemble in one pass` diye bildirir (sınır bir komut satırı uzunluğudur; geçişli kurguda yüzlerce, yalnızca kesmeli kurguda binden fazla çekim) ve kurguyu bölmeniz gerekir.
- Bir projede aynı anda tek render çalışır; ikincisi `another render is already running for this project` diyerek durur. Seçici de aynı anda tek iş çalıştırır.
- Kurgu müzikten uzun olamaz.
- Vuruş tespiti sabit tempo varsayar; seçicide `period` ve `t0` değerlerine bakın, gerekirse elle düzeltin.

## Yol haritası

İlk sürümde olmayanlar:

- CapCut taslak aktarımı
- Yüz tespiti ve tanıma
- Ufuk eğimi düzeltme
- Müziği kısaltmak için dikiş noktası bulma
- Ses efekti üretimi ve yerleştirme
- HDR kaynaklar için ton eşleme
- Eski araçlardaki projelerin dönüştürülmesi
- PyPI paketi ve Claude Code eklenti pazarında yer alma

## Testler

```
pip install -r requirements-dev.txt
python -m pytest
```

Bu, hızlı testleri çalıştırır. Yavaş testler gerçek video render eder ve ffmpeg ister: `python -m pytest -m slow`.

## Lisans

Kod MIT lisanslıdır (bkz. `LICENSE`). `examples/demo/` içindeki video klipler (`*.mp4`) ve onlardan üretilen `docs/demo.gif` MIT kapsamında değildir; depo sahibinin kendi çekimleridir, bütün hakları saklıdır ve yalnızca aracı denemek için depodadır. Demo müziği "Our expanse" (yazar Bobjt) CC0 1.0 (kamu malı) lisanslıdır. Bu durum `NOTICE` ve ayrıntıların bulunduğu `examples/demo/CREDITS.md` dosyalarında yazılıdır.
