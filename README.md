# AKS third-party image reporter

AKS üzerinde çalışan container image'larını toplar ve tekilleştirilmiş image
listesini Azure OpenAI'a göndererek `internal`, `third_party` veya `unknown`
olarak sınıflandırır. Statik image listesi veya eşleşme kuralı kullanılmaz.
Python 3.10+ yeterlidir; ek Python paket bağımlılığı yoktur.

## Yapılandırma

Aşağıdaki ortam değişkenlerini çalıştırdığınız ortamda tanımlayın:

| Değişken | Açıklama |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Kaynak kök HTTPS adresi, ör. `https://YOUR-RESOURCE.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | Chat completions ve JSON mode destekleyen model deployment adı |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API anahtarı; secret olarak enjekte edin |
| `AZURE_OPENAI_API_VERSION` | İsteğe bağlı; varsayılan `2024-10-21` |

Anahtarı kaynak koda veya komut satırı argümanlarına yazmayın. Uygulama mevcut
Azure kaynağını kullanır; kaynak veya deployment oluşturmaz. Key tabanlı
authentication desteklenir; Entra ID/managed identity henüz desteklenmez.

Canlı tarama için `kubectl` ve AKS erişimi olan kubeconfig gerekir:

```bash
python eol_reporter.py --context YOUR_AKS_CONTEXT
python eol_reporter.py --context YOUR_AKS_CONTEXT --category third_party --format csv
python eol_reporter.py --context YOUR_AKS_CONTEXT --category unknown
```

Tüm namespace'lerde pod, ReplicaSet ve Job listeleme yetkisi gerekir.
`--namespace NAME` ile kapsamı daraltabilirsiniz. Cluster'a yalnızca okuma yapılır.
Deployment/CronJob owner zinciri takip edilir. Yalnızca Running fazındaki podların
şu anda çalışan normal, init sidecar ve ephemeral container'ları dahil edilir.
Tamamlanmış init ve bekleyen/crash durumundaki container'lar dahil değildir.

## Şirket bağlamı ve sınıflandırma

Model private registry'deki nginx gibi upstream yazılımları da üçüncü taraf
olarak değerlendirmeye yönlendirilir. Private registry veya tanınmayan bir ad
tek başına şirket içi sahiplik kanıtı sayılmaz. Modelin bildiği ürünlerden ve
sağlanan bağlamdan karar veremediği image'lar `unknown` kalmalıdır.

İsteğe bağlı olarak şirketinizin geliştirdiği ürünleri ve adlandırma yaklaşımını
anlatan bir metin dosyası sağlayabilirsiniz:

```bash
python eol_reporter.py --company-context /path/to/company-context.txt
```

Bu metin ve image referansları Azure OpenAI endpoint'inize gönderilir. Namespace,
pod adları ve workload metadata'sı gönderilmez. Model yanıtı sahiplik doğrulaması
değildir; yanlış sınıflandırabilir ve registry içeriğini incelemez. Her kararla
birlikte `classification_reason` ve `classification_source` raporlanır. Şirket
bağlamı verilmezse şirket içi uygulamaların bir bölümü `unknown` kalabilir.

Image'lar tekilleştirilir; istekler varsayılan 50 image'lık gruplarla gönderilir
(`--batch-size`). Her tarama yeniden model çağrısı yapar ve ücret oluşturabilir.
API hatası, eksik/tekrarlı image, geçersiz kategori veya kesilmiş yanıt işlemi
başarısız kılar; kısmi rapor başarıyla tamamlanmış gibi yazılmaz. Otomatik retry
henüz yoktur; 429 durumunda daha sonra tekrar deneyin.

## Yerel veri ve testler

```bash
python -m unittest discover -s tests -v
kubectl --context YOUR_AKS_CONTEXT get pods,replicasets.apps,jobs.batch -A -o json > /tmp/inventory.json
python eol_reporter.py --input /tmp/inventory.json
python eol_reporter.py --input /tmp/inventory.json --inventory-only
```

`--input` cluster'a bağlanmaz; varsayılan olarak yine Azure OpenAI'ı kullanır.
`--inventory-only` Azure çağrısını atlar; tüm kayıtlar `unknown` ve kaynak `none`
olarak işaretlenir. JSON/CSV çıktısı namespace, workload, image, container türü,
container sayısı, pod adları ve runtime image ID'lerini içerir.

Testler Azure HTTP çağrılarını taklit eder; canlı AKS/Azure erişimini doğrulamaz.
EOL ve güvenlik açığı analizi kapsam dışıdır.
