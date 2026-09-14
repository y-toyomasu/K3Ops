# K3Ops Safe MVP

K3Opsは、Product Roadmap、Development Operations RoadmapとTask Metadataを安全に読み取り、Roadmapごとに独立して決定論的に集計するread-only Dashboardです。Task、Agent、Git、文書またはProduct Codeを変更する機能はありません。

## 公開境界

ComposeはDashboardを`0.0.0.0:8890`で全IPv4 Interfaceへ公開します。認証とTLSはありません。Port 8890へ到達できる端末はallowlist済みTask Metadataを閲覧できるため、所有または明示的に許可されたNetworkだけで使用し、Internetへ公開しないでください。必要に応じてHost Firewallで到達範囲を制限してください。

K3Opsは外部Network、CDN、外部Font、Telemetry、Router Port ForwardまたはFirewall変更を使用しません。UI AssetはApplication内に保持します。

## Codex Agent Observation Hook bundle

`ops/codex-agent-observation/`は、Codexの`SubagentStart`だけを対象にする手動導入用の配布bundleです。配布`hooks.json`とHandler Sourceをプロジェクトの`.codex/`へコピーし、Project Trustを確認する操作はProduct Ownerが手動で行います。K3OpsのGit管理対象にあるbundleを直接有効化したり、実行時コピーを作成したりはしません。

Handlerは公式Hook入力の`model`だけをObserved Modelとして扱います。Reasoning Effortは観測せず、固定の未観測理由を記録します。成功時は固定形式のObservation ReferenceとObserved Modelだけを子Agent contextへ渡します。入力が無効、Hook未導入、Project未信頼、未読込、runtime更新失敗またはHandler失敗でも、SubAgentの起動を妨げず、contextを追加しません。

手動導入後のruntimeはプロジェクト相対の`runtime/agent-observations/`に置かれ、Git管理外です。各recordにはObservation Reference、UTC時刻、Observed Model、固定Observation SourceおよびReasoningの未観測理由だけを残します。Session／Agent識別子、transcript、Prompt、Command／Error本文、Host固有絶対Path、Secret、Credential、Token、Flag、認証情報および非公開思考は保存・出力・引継ぎしません。更新は排他・原子的置換で行い、30日または100件のいずれか早い上限を超える古いrecordを削除します。

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

Containerへ渡すHost Dataは次の4つだけです。

- `project-management/roadmaps/development-operations.toml`からContainer内`/data/roadmap/development-operations.toml`へのread-only単一File mount
- `project-management/roadmaps/product.toml`からContainer内`/data/roadmap/product.toml`へのread-only単一File mount
- `tasks/`からContainer内`/data/tasks`へのread-only Directory mount
- `stories/INDEX.md`からContainer内`/data/stories/INDEX.md`へのread-only単一File mount

Workspace全体、`.git`、`.worktrees`、`repositories`、`PRODUCT.md`、`DECISIONS.md`、`ARCHITECTURE.md`、個別Story本文またはSecretをmount・探索しません。Container filesystemもread-onlyで、全Linux Capabilityをdropし、`no-new-privileges`を有効にします。

## Endpoint

- `GET /`: 独立したProduct Summary／Roadmap、Development Operations Summary／Roadmap、Task一覧、Story Index一覧
- `GET /health`: Process liveness。Data Sourceが不正でもProcess稼働中なら`200`
- `GET /api/dashboard`: 既存Fieldに加えて`product_summary`、`product_roadmap`、`stories`、`story_summary`、`story_warnings`を含む正規化済みDashboard JSON

状態変更Endpointはありません。`POST`、`PUT`、`PATCH`、`DELETE`による操作は提供しません。Browser ReloadごとにRoadmapとTask Metadataを再読込します。

## Data boundary

RoadmapとTaskの解析・集計は`project-management/k3ops-data-contract.md`に従います。二つのRoadmapはFile単位で独立Parseし、Item、Summary、Filter、WarningおよびFailure状態をmergeしません。一方が不正でも、他方とTask一覧は安全に表示できます。Taskから表示・API返却する情報はTask ID、Title、Status、Priority、Dependencies、Source、Claimed By、Claimed Atだけです。UIのTask一覧はそのうちSourceを除く運用項目だけを表示します。Roadmapの`references`はAPI／UIへ返しません。

不正TOML、欠落Task、不正Status、通常File以外、Symlink、Size／件数／Schema上限超過はSourceを識別できる正規化Warningとして扱います。Raw Error、Task本文、Implementation、Findings、Deviations、GUI Feedback、絶対Path、Commit／Branch詳細、Secret、Credential、Token、Flag、Hint本文、配置Path、正解経路、認証情報または非公開思考はAPI/UIへ返しません。

ProductとDevelopment Operationsには独立した自由入力、Workstream、Horizon Filterがあります。60秒更新、Background更新、表示復帰時の即時更新、重複Request抑止、失敗時のstale表示と回復は両表示へ適用します。

Story表示は`stories/INDEX.md`の固定見出しと5列（Story、Status、Topic、Last Updated、Resume From）だけを安全に読みます。個別Story本文、Handoff、Task Lead指示やStory以外の管理Repository情報は表示・API返却しません。Indexが欠落、不正File種別、symlink、Size・件数・UTF-8・Header・Row・重複IDのいずれかで不正な場合、warningと空のStory表示に切り替え、既存Dashboardは維持します。

## Test

```powershell
docker build -t k3ops-safe-mvp .
docker run --rm k3ops-safe-mvp python -m unittest discover -s tests -v
docker compose config
```

Testはfixtureだけを使用し、外部Networkや実際のTask変更を行いません。
