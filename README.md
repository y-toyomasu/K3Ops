# K3Ops Safe MVP

K3Opsは、Product Roadmap、Development Operations RoadmapとTask Metadataを安全に読み取り、Roadmapごとに独立して決定論的に集計するread-only Dashboardです。Task、Agent、Git、文書またはProduct Codeを変更する機能はありません。

## 公開境界

ComposeはDashboardを`0.0.0.0:8890`で全IPv4 Interfaceへ公開します。認証とTLSはありません。Port 8890へ到達できる端末はallowlist済みTask Metadataを閲覧できるため、所有または明示的に許可されたNetworkだけで使用し、Internetへ公開しないでください。必要に応じてHost Firewallで到達範囲を制限してください。

K3Opsは外部Network、CDN、外部Font、Telemetry、Router Port ForwardまたはFirewall変更を使用しません。UI AssetはApplication内に保持します。

## 起動

管理Repositoryのrootを`<management-root>`とします。K3Ops Repositoryで管理rootを指定して起動します。

```powershell
$env:K3OPS_MANAGEMENT_ROOT = '<management-root>'
docker compose up --build -d
```

Dashboard: `http://localhost:8890`

停止:

```powershell
docker compose down
```

`K3OPS_MANAGEMENT_ROOT`を省略した場合、Composeは`repositories/K3Ops`から見た既定の`../..`を使用します。

## read-only mount

Containerへ渡すHost Dataは次の3つだけです。

- `project-management/roadmaps/development-operations.toml`からContainer内`/data/roadmap/development-operations.toml`へのread-only単一File mount
- `project-management/roadmaps/product.toml`からContainer内`/data/roadmap/product.toml`へのread-only単一File mount
- `tasks/`からContainer内`/data/tasks`へのread-only Directory mount

Workspace全体、`.git`、`.worktrees`、`repositories`、`PRODUCT.md`、`DECISIONS.md`、`ARCHITECTURE.md`またはSecretをmount・探索しません。Container filesystemもread-onlyで、全Linux Capabilityをdropし、`no-new-privileges`を有効にします。

## Endpoint

- `GET /`: 独立したProduct Summary／Roadmap、Development Operations Summary／Roadmap、Task一覧
- `GET /health`: Process liveness。Data Sourceが不正でもProcess稼働中なら`200`
- `GET /api/dashboard`: 既存Fieldに加えて`product_summary`と`product_roadmap`を含む正規化済みDashboard JSON

状態変更Endpointはありません。`POST`、`PUT`、`PATCH`、`DELETE`による操作は提供しません。Browser ReloadごとにRoadmapとTask Metadataを再読込します。

## Data boundary

RoadmapとTaskの解析・集計は`project-management/k3ops-data-contract.md`に従います。二つのRoadmapはFile単位で独立Parseし、Item、Summary、Filter、WarningおよびFailure状態をmergeしません。一方が不正でも、他方とTask一覧は安全に表示できます。Taskから表示・API返却する情報はTask ID、Title、Status、Priority、Dependencies、Source、Claimed By、Claimed Atだけです。UIのTask一覧はそのうちSourceを除く運用項目だけを表示します。Roadmapの`references`はAPI／UIへ返しません。

不正TOML、欠落Task、不正Status、通常File以外、Symlink、Size／件数／Schema上限超過はSourceを識別できる正規化Warningとして扱います。Raw Error、Task本文、Implementation、Findings、Deviations、GUI Feedback、絶対Path、Commit／Branch詳細、Secret、Credential、Token、Flag、Hint本文、配置Path、正解経路、認証情報または非公開思考はAPI/UIへ返しません。

ProductとDevelopment Operationsには独立した自由入力、Workstream、Horizon Filterがあります。60秒更新、Background更新、表示復帰時の即時更新、重複Request抑止、失敗時のstale表示と回復は両表示へ適用します。

## Test

```powershell
docker build -t k3ops-safe-mvp .
docker run --rm k3ops-safe-mvp python -m unittest discover -s tests -v
docker compose config
```

Testはfixtureだけを使用し、外部Networkや実際のTask変更を行いません。
