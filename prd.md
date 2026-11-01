# PRODUCT REQUIREMENTS DOCUMENT (PRD)
## Wedding Invitation Builder

**Versi:** 1.0  
**Status:** Product Specification  
**Model:** SaaS — B2C & B2B  
**Target Pasar:** Indonesia

---

# 1. Product Overview

Wedding Invitation Builder adalah platform SaaS yang memungkinkan pengguna membuat undangan pernikahan digital melalui template yang dapat dikustomisasi tanpa kemampuan desain grafis atau pemrograman.

Pengguna dapat memilih template, mengisi informasi pernikahan, mengunggah foto, mengatur tampilan, melakukan preview, dan menerbitkan undangan dalam bentuk URL yang dapat dibagikan kepada tamu.

Platform memiliki dua segmen pengguna:

- **B2C:** Calon pengantin yang membuat undangan secara mandiri.
- **B2B:** Wedding Organizer, vendor pernikahan, dan studio undangan yang mengelola banyak undangan untuk klien.

Kedua segmen menggunakan sistem builder yang sama, tetapi memiliki mekanisme pembayaran dan hak akses yang berbeda.

## 1.1 Product Vision

Menjadi platform pembuatan undangan pernikahan digital yang mudah digunakan, fleksibel, terjangkau, dan dapat dimanfaatkan baik oleh individu maupun bisnis wedding.

## 1.2 Problem Statement

Permasalahan yang ingin diselesaikan:

1. Pembuatan undangan digital secara custom membutuhkan kemampuan desain dan waktu.
2. Calon pengantin membutuhkan solusi undangan yang praktis dan terjangkau.
3. Vendor dan WO kesulitan mengelola banyak undangan dari berbagai klien.
4. Pengelolaan RSVP dan daftar tamu sering dilakukan secara manual.
5. Pengguna membutuhkan fleksibilitas dalam mengatur tampilan undangan tanpa harus membuat website sendiri.

## 1.3 Product Goals

- Mempermudah pembuatan undangan digital.
- Menyediakan template profesional dan responsif.
- Menyediakan editor yang mudah digunakan.
- Mendukung publikasi undangan menggunakan URL unik.
- Mengintegrasikan RSVP, guestbook, dan pengelolaan tamu.
- Menghasilkan pendapatan dari pembelian Premium dan langganan Business.
- Mendukung pengelolaan banyak proyek melalui workspace.

---

# 2. Target Users

## 2.1 B2C — Personal User

Target:
- Calon pengantin.
- Pasangan yang ingin membuat undangan sendiri.
- Pengguna yang membutuhkan undangan digital dengan harga terjangkau.

Kebutuhan:
- Memilih template.
- Mengedit konten dan tampilan.
- Mengunggah foto.
- Mengatur acara.
- Menerbitkan undangan.
- Melihat RSVP dan ucapan tamu.

## 2.2 B2B — Business User

Target:
- Wedding Organizer.
- Vendor undangan digital.
- Studio desain.
- Agency wedding.

Kebutuhan:
- Mengelola banyak klien.
- Membuat banyak proyek undangan.
- Mengelola anggota tim.
- Menggunakan template premium.
- Mengelola daftar tamu dan RSVP klien.
- Melihat status dan performa setiap undangan.

---

# 3. Product Roles

| Role | Tanggung Jawab |
|---|---|
| Platform Admin | Mengelola keseluruhan platform |
| Personal User | Membuat undangan pribadi |
| Workspace Owner | Mengelola workspace bisnis |
| Workspace Admin | Mengelola proyek dan anggota |
| Workspace Editor | Membuat dan mengedit undangan |
| Client / Collaborator | Memberi masukan atau mengakses proyek sesuai izin |

Role platform berbeda dengan role workspace. Satu user dapat menjadi Personal User sekaligus Workspace Owner.

---

# 4. Product Modules

## 4.1 Authentication & Account

Fitur:
- Register.
- Login.
- Logout.
- Forgot password.
- Reset password.
- Profile management.
- Account verification.

## 4.2 Template Gallery

Fitur:
- Menampilkan seluruh template.
- Kategori template.
- Filter berdasarkan gaya.
- Preview template.
- Penanda Free dan Premium.
- Responsive preview.

Kategori awal:
- Minimalist.
- Elegant.
- Modern.
- Floral.
- Luxury.
- Traditional.
- Islamic.
- Rustic.

Setiap template memiliki informasi:
- Nama.
- Thumbnail.
- Kategori.
- Status Free/Premium.
- Konfigurasi section.
- Default color palette.
- Default typography.

## 4.3 Wedding Editor

Editor menggunakan pendekatan section-based.

Pengguna dapat:
- Mengubah teks.
- Mengunggah foto.
- Mengatur warna.
- Memilih font.
- Mengaktifkan atau menonaktifkan section.
- Mengatur urutan section yang didukung template.
- Mengatur musik.
- Melihat live preview.

Section undangan:

1. Cover.
2. Couple Information.
3. Wedding Event.
4. Love Story.
5. Gallery.
6. Location.
7. RSVP.
8. Guestbook.
9. Gift Information.
10. Closing.

Editor tidak menggunakan kebebasan drag-and-drop penuh pada MVP.

## 4.4 Media Management

Fitur:
- Upload foto.
- Upload musik.
- Validasi format dan ukuran.
- Penyimpanan file pada object storage.
- Penghapusan media.
- Pembatasan kuota berdasarkan paket.

File harus divalidasi di backend.

Format media yang diizinkan harus dapat dikonfigurasi oleh platform admin.

## 4.5 Invitation Management

Fitur:
- Membuat undangan.
- Mengedit undangan.
- Mengubah slug.
- Preview undangan.
- Publish.
- Unpublish.
- Duplicate invitation.
- Melihat status undangan.
- Mengelola masa aktif undangan.

Status invitation:
- draft.
- published.
- unpublished.
- expired.
- archived.

## 4.6 Public Invitation

Setiap undangan yang diterbitkan memiliki halaman publik.

Fitur:
- Cover undangan.
- Informasi mempelai.
- Detail acara.
- Countdown.
- Gallery.
- Google Maps.
- Musik.
- RSVP.
- Guestbook.
- Gift information.
- Closing.

Undangan Free menampilkan branding platform.

Undangan Premium yang aktif tidak menampilkan branding platform.

## 4.7 RSVP & Guestbook

RSVP:
- Nama tamu.
- Jumlah kehadiran jika diizinkan.
- Status kehadiran.
- Pesan tambahan.
- Waktu pengiriman.

Guestbook:
- Nama pengirim.
- Pesan ucapan.
- Waktu pengiriman.
- Moderasi pesan oleh pemilik undangan.

## 4.8 Guest Management

Fitur:
- Tambah tamu.
- Edit tamu.
- Hapus tamu.
- Import daftar tamu.
- Export daftar tamu.
- Kategori tamu.
- Generate personal invitation link.
- Generate QR code.
- Check-in tamu.

## 4.9 Analytics

Data yang dapat ditampilkan:
- Jumlah pengunjung.
- Jumlah RSVP.
- Jumlah tamu hadir.
- Jumlah tamu tidak hadir.
- Jumlah ucapan.
- Statistik pengunjung berdasarkan periode.

Data analytics harus dibedakan dari jumlah RSVP. Pengunjung tidak selalu merupakan tamu yang sudah mengonfirmasi kehadiran.

## 4.10 Business Workspace

Fitur:
- Membuat workspace.
- Mengatur profil bisnis.
- Mengundang staf.
- Mengelola role staf.
- Membuat proyek undangan.
- Menambahkan klien.
- Mengelola banyak proyek.
- Melihat status proyek.
- Mengelola langganan bisnis.

Setiap proyek undangan milik workspace memiliki satu klien atau pasangan sebagai pihak terkait.

---

# 5. Monetization & Pricing

Harga berikut merupakan harga awal untuk validasi pasar.

## 5.1 B2C — Personal Purchase

### Free — Rp0

- Template Free.
- Editor dasar.
- Maksimal 5 foto.
- Musik dari katalog.
- RSVP dasar.
- Guestbook dasar.
- Maksimal 1 undangan aktif.
- Subdomain platform.
- Branding platform pada undangan.

### Premium — Rp99.000

Sekali bayar untuk satu undangan.

- Semua template.
- Maksimal 30 foto.
- Upload musik sendiri.
- Custom warna dan font.
- RSVP lengkap.
- Guestbook lengkap.
- Custom slug.
- Tanpa branding platform.
- QR Check-in.
- Analytics.

Masa aktif: 1 tahun sejak undangan pertama kali dipublikasikan setelah pembelian Premium.

### Premium Plus — Rp149.000

Sekali bayar untuk satu undangan.

- Semua fitur Premium.
- Maksimal 60 foto.
- Custom domain.
- Kapasitas penyimpanan lebih besar.
- Fitur pengelolaan tamu tambahan.
- Prioritas bantuan teknis.

Masa aktif: 1 tahun sesuai tanggal aktivasi paket.

## 5.2 B2B — Workspace Subscription

### Business Starter — Rp199.000/bulan

- Maksimal 5 undangan aktif.
- Maksimal 2 staf.
- Semua fitur builder.
- Template Premium.
- Dashboard klien.
- Pengelolaan RSVP.
- Pengelolaan proyek.

### Business Pro — Rp399.000/bulan

- Maksimal 20 undangan aktif.
- Maksimal 5 staf.
- Semua fitur Starter.
- Branding bisnis.
- Export data tamu.
- Laporan bisnis.
- Manajemen klien.

### Agency — Rp799.000/bulan

- Maksimal 50 undangan aktif.
- Maksimal 10 staf.
- Semua fitur Pro.
- Custom branding lebih lengkap.
- Prioritas support.
- Fitur operasional bisnis.

Batas proyek dihitung berdasarkan undangan aktif, bukan seluruh undangan yang pernah dibuat.

---

# 6. Access Control & Entitlements

Sistem hak akses tidak boleh hanya berdasarkan role user.

Hak akses ditentukan berdasarkan:
1. Sumber kepemilikan undangan.
2. Paket yang aktif.
3. Masa berlaku paket.
4. Fitur yang diizinkan.
5. Batas penggunaan fitur.

## 6.1 Feature Lock

Contoh:
- custom_music.
- premium_templates.
- custom_domain.
- remove_branding.
- qr_checkin.
- advanced_analytics.

Jika fitur tidak tersedia, API mengembalikan HTTP 403 dengan kode FEATURE_UPGRADE_REQUIRED.

## 6.2 Usage Limit

Contoh:
- max_photos.
- max_storage_mb.
- max_active_invitations.
- max_staff.
- max_guest_records.

Jika kuota habis, API mengembalikan HTTP 403 atau 409 sesuai jenis konflik dan kode USAGE_LIMIT_REACHED.

## 6.3 Access Resolution

Untuk personal invitation:
- Free menggunakan default Free plan.
- Premium menggunakan personal purchase yang aktif.

Untuk business invitation:
- Hak akses berasal dari subscription workspace yang aktif.
- Paket bisnis berlaku bagi proyek undangan milik workspace.
- Pembelian personal tidak boleh otomatis mengambil alih hak akses undangan milik workspace.

Jika workspace memiliki subscription kedaluwarsa, undangan tidak otomatis dihapus. Sistem memberikan masa tenggang dan pembatasan sesuai kebijakan.

## 6.4 Branding Rules

- Free: watermark platform wajib.
- Premium: watermark platform dihilangkan.
- Premium Plus: watermark dihilangkan.
- Business: branding mengikuti paket dan konfigurasi workspace.

Watermark tidak boleh hanya dikontrol oleh frontend. Backend harus memberikan konfigurasi branding berdasarkan entitlement undangan.

---

# 7. Payment & Subscription

## 7.1 B2C Payment Flow

1. Pengguna memilih paket Premium.
2. Backend memeriksa kepemilikan dan status undangan.
3. Backend membuat order.
4. Payment gateway menghasilkan instruksi pembayaran.
5. Pengguna melakukan pembayaran.
6. Payment gateway mengirim webhook.
7. Backend memvalidasi signature dan status transaksi.
8. Backend memperbarui status pembayaran.
9. Backend mengaktifkan personal purchase.
10. Hak akses undangan diperbarui.

Premium aktif selama satu tahun sejak publikasi pertama setelah pembayaran berhasil. Jika belum dipublikasikan, masa aktif belum dimulai.

## 7.2 B2B Subscription Flow

1. Workspace memilih paket bisnis.
2. Backend membuat subscription order.
3. Pengguna membayar.
4. Webhook diverifikasi.
5. Subscription workspace diaktifkan.
6. Workspace memperoleh kuota dan hak akses.
7. Sistem memproses perpanjangan pada periode berikutnya.

## 7.3 Payment Status

- pending.
- paid.
- failed.
- expired.
- refunded.
- cancelled.

## 7.4 Subscription Status

- active.
- past_due.
- cancelled.
- expired.

Pembatalan langganan menghentikan perpanjangan berikutnya, tetapi akses tetap tersedia sampai akhir periode yang sudah dibayar.

## 7.5 Payment Security

- Verifikasi signature webhook.
- Idempotency.
- Penyimpanan provider reference.
- Audit transaksi.
- Rekonsiliasi pembayaran.
- Pencatatan refund.
- Tidak mempercayai redirect sukses dari frontend sebagai bukti pembayaran.

---

# 8. Business Rules

1. Satu user dapat memiliki akun personal dan bergabung dengan beberapa workspace.
2. Satu undangan hanya memiliki satu jenis kepemilikan: personal atau workspace.
3. Undangan personal tidak membutuhkan workspace.
4. Undangan bisnis harus terhubung dengan workspace.
5. Personal purchase berlaku untuk satu undangan.
6. Business subscription berlaku pada workspace.
7. Premium personal berlaku satu tahun sejak publikasi pertama setelah pembelian.
8. Undangan Free dapat dipublikasikan dengan watermark.
9. Fitur premium harus diperiksa oleh backend.
10. Pembayaran hanya dianggap berhasil setelah webhook valid.
11. Webhook berulang tidak boleh menghasilkan aktivasi ganda.
12. Undangan yang kedaluwarsa tidak boleh langsung dihapus.
13. Penghapusan file harus mempertimbangkan masa retensi dan kepemilikan.
14. Perubahan paket tidak boleh merusak data undangan yang sudah dibuat.
15. Workspace hanya dapat mengelola proyek sesuai izin anggotanya.

---

# 9. Non-Functional Requirements

## Security
- Password hashing.
- Authentication dan authorization.
- Workspace data isolation.
- Ownership validation.
- Rate limiting.
- Secure file upload.
- Signed webhook validation.
- Audit log untuk tindakan penting.

## Performance
- Halaman publik harus ringan.
- Optimasi ukuran foto.
- Lazy loading media.
- Caching untuk template dan konfigurasi publik.
- Background processing untuk pekerjaan berat.

## Scalability
- Pemisahan modul backend.
- Object storage untuk media.
- Database indexing.
- Background jobs.
- Konfigurasi paket yang dapat diperbarui tanpa perubahan kode.

## Responsive Design
- Mobile-first untuk halaman undangan.
- Editor responsif.
- Dashboard desktop dan mobile.
- Kompatibilitas browser modern.

## Reliability
- Backup database.
- Monitoring error.
- Logging transaksi.
- Retry untuk background jobs.
- Idempotent payment webhook.

---

# 10. MVP Scope

## Phase 1 — Core Builder

- Authentication.
- User dashboard.
- Template gallery.
- 5–8 template awal.
- Template preview.
- Section-based editor.
- Upload foto.
- Pengaturan warna dan font.
- Publish undangan.
- Public invitation.
- Watermark Free.

## Phase 2 — Monetization

- Plan management.
- Feature entitlements.
- Usage limits.
- Personal purchase.
- Payment gateway.
- Webhook.
- Premium activation.
- Expiration management.

## Phase 3 — Wedding Features

- RSVP.
- Guestbook.
- Guest management.
- QR check-in.
- Analytics.

## Phase 4 — Business Workspace

- Workspace.
- Workspace members.
- Role management.
- Client management.
- Multi-project.
- Business subscriptions.
- Subscription limits.

---

# 11. Success Metrics

Indikator keberhasilan platform:

- Jumlah akun terdaftar.
- Jumlah undangan dibuat.
- Persentase undangan yang berhasil dipublikasikan.
- Konversi Free ke Premium.
- Jumlah workspace bisnis aktif.
- Monthly Recurring Revenue (MRR) B2B.
- Jumlah transaksi B2C.
- Tingkat pembatalan subscription.
- Jumlah undangan yang dikelola setiap workspace.

---

# 12. Out of Scope untuk MVP

Fitur berikut tidak menjadi prioritas versi pertama:

- AI-generated wedding design.
- Full drag-and-drop editor.
- Mobile application native.
- Marketplace vendor.
- Chat antar pengguna.
- Sistem pembayaran untuk vendor kepada klien.
- White-label penuh untuk seluruh paket bisnis.

---

# 13. Definition of Done

MVP dinyatakan siap digunakan ketika:

1. User dapat mendaftar dan login.
2. User dapat memilih template.
3. User dapat mengedit undangan.
4. User dapat melakukan preview.
5. User dapat mempublikasikan undangan Free dengan watermark.
6. User dapat membeli Premium.
7. Backend dapat memvalidasi webhook.
8. Premium hanya aktif setelah pembayaran valid.
9. Hak akses fitur diperiksa di backend.
10. Undangan publik dapat dibuka melalui URL unik.
11. Data antar pengguna tidak dapat diakses tanpa izin.
12. Halaman undangan responsif di perangkat mobile dan desktop.

---

**End of PRD v1.0**