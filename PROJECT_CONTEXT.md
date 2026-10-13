# ConceptFlow for Blender — PROJECT_CONTEXT

> 新しいセッション（Claude Code のクラウドセッションなど）は、**最初にこのファイルを全部読む**こと。
> 作業を終えたら、変わった点（実装済み・未実装・既知の問題・決定事項）をこのファイルに反映する。

- 最終更新: 2026-10-12
- アドオンのバージョン: **0.3.0（開発中）**（`conceptflow/__init__.py` の `bl_info`）
- 対象 Blender: **4.5.4 LTS**（EEVEE Next）で開発・検証。`bl_info` の最低バージョンは 4.2.0 だが、4.2 での動作は未検証。
- 状態: v0.1・v0.2 は実装・テスト済み。**v0.3: Auto Variations、基本的な Compare & Apply、Read Views（GPUオーバーレイ方式）はコード実装済み（2026-10-09、Blender 実機では未検証）**（第8章・第11章）。

---

## 1. 製品概要

「ConceptFlow for Blender」は、**販売予定**のBlenderアドオン。コンセプトは「3Dを作るためのBlenderではなく、絵を考えるためのBlenderへ」。
コア体験は「ボタン1つで8案。選んだら、そのまま反映」。

30秒デモの想定: グレーBOXで部屋を組む → HORROR を1クリック → 8案生成 → 1枚選んで Apply → ボタン1つで本番アセットに差し替え。

- 作者・依頼主: 小坂正明さん。コンセプトアーティスト（**プログラマーではない**）。日本語でやり取りする。報告は短く。
- 小坂さんには個人用の「Conceptシリーズ」アドオン群（concept_ground_paint、concept_puddle、concept_material_mixer、concept_scatter、concept_weathering、concept_environment、concept_light_rig など）があるが、**ConceptFlow はそれとは完全に別の製品**。
- 仕様の出どころ: 「ConceptFlow コンセプトシート v2」（claude.ai の Artifact: https://claude.ai/artifact/17VCTaNoj4pTqkLDg2CANh 、Artifact ツールの read で `path="project/Main.dc.html"`）。小坂さんのアカウントでしか開けない。要点は本ファイルに書いてある。
- 進捗ボード: ピン留めの「Concept制作ボード」Artifact（https://claude.ai/artifact/MyKaRu9nizRz6RhkimxHjx）の `tools` コレクション、`doc_id = cf-mvp`。`note` だけを更新し、`status="done"` は MVP 完成＋常に有効化後のみ。他の行は触らない。更新時は読んだ `version` を `if_version` に付ける（2026-10-08 時点で version 5）。

## 2. 守るルール（常に有効）

### 製品・コード
1. **単体で動くこと。** Conceptシリーズのモジュールを import しない。使いたい処理はコピーして ConceptFlow 用に書き直す。Conceptシリーズのコードとデータ（`CGP_` `CCB_` `CPD_` `CMX_` `CSC_` `CWX_` `CEN_` `CLR_` `CTM_` `CNK_` など）は**変更しない**。
2. 新しく作るデータ名は `CF_` で始める（コレクション、ワールド、ライト、カスタムプロパティ名など）。
3. **UI は英語が基本、日本語翻訳を付ける**（`translations.py`）。海外でも販売する前提。ツールチップを付ける。アイコン名は `bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items` に存在するものだけを使う（`common.icon()`）。
4. サイドバー（3Dビュー > N）に専用タブ「ConceptFlow」。機能ごとにパネルを分ける。
5. **元に戻せること（Undo・リセット）、エラーで止まらないこと（`@guarded`）、重くならないこと**を重視。
6. 新しい演算子の `execute` は `common.guarded` で包む。Undo 対象の演算子はエラー時も `FINISHED` を返し、Ctrl+Z で戻せるようにする。
7. UI 文字列を追加したら `translations.py` に日本語を足し、`tools/i18n_check.py` で抜けを確認する。
8. MVP では Lite/Pro の機能制限は付けない（機能セットは1つ）。
9. 「絵の点数付け」はしない（Read Views は表示するだけ。判断は人に任せる）。
10. MVP に入れないもの: Reference Board、Composition Solver、進化型生成、Change Detector、カスタムプリセット、Concept Board。

### テスト時の安全ルール（Blender を操作するセッション）
- 小坂さんは同じ Blender で別の作業をしている可能性がある。**既存シーン（Scene、CB_Test、CEN_Test、CPO_Test など）の設定は変更しない。** テストは専用シーン **`CF_Test`** の中だけで行う（自前のカメラ `CF_Cam`、ワールド `CF_World`、ライトを持つ）。
- アクティブシーン・モード・選択・アクティブオブジェクト・ビューポートのシェーディングを勝手に変えない。`bpy.context.temp_override(scene=..., view_layer=..., selected_objects=...)` で CF_Test に対して演算子を実行し、レンダーは `bpy.ops.render.render(scene="CF_Test", write_still=True)` で行う。
- ウィンドウのシーンを切り替えて見せる必要があるときは、先にチャットで一言断り、確認後すぐ元に戻す。
- テスト用に作ったもの（CF_Test シーン、`CF_World_*` ワールド、`CF_Assets*` コレクション、`T_*` テスト用メッシュなど）は、作業の最後に「残すか消すか」を小坂さんに聞く。
- 完成時に「アドオンとして常に有効にしますか？」と聞く。了承されたら `addon_utils.enable("conceptflow", default_set=True, persistent=True)` → `bpy.ops.wm.save_userpref()`。販売用パッケージ（`blender_manifest.toml` 付きの拡張機能 zip、ライセンス表記を含む）は、MVP 完成後に小坂さんと相談してから作る。
- 各バージョンを動く形で作って見せ、**短く報告して確認を取ってから**次へ進む（リリースは急がない）。

---

## 3. リポジトリ構成

```
.
├─ conceptflow/              ← Blender アドオン本体（これを addons フォルダに置く）
│   ├─ __init__.py           bl_info、モジュール登録（失敗時はロールバック）
│   ├─ common.py             共通処理（翻訳ショートカット、icon()、@guarded、コレクション/メッシュ補助）
│   ├─ props.py              PropertyGroup 一式（Scene.conceptflow / Object.conceptflow）
│   ├─ state.py              Branch Tree 用の状態キャプチャ/復元（プロバイダー方式）
│   ├─ graybox.py            グレーBOX（壁・柱・階段・ドア・窓・床・ブロック、建物生成、開口部）
│   ├─ building_extras.py    空間スタイル／廃墟化／壁クリックでドア・窓／部屋に家具を散らす／スケール人物／下絵／庇・アーチ壁・寄棟・ドーム
│   ├─ graybox_tools.py      壁を描く（クリック）／小物・家具／階段の種類／街並み
│   ├─ branch_tree.py        分岐ツリー（保存・移動・上書き・削除・チェックポイント）
│   ├─ look_presets.py       Look Presets（非破壊ライティング 5種）
│   ├─ asset_swap.py         アセット差し替え（グレーBOX ⇄ 本番アセット）
│   ├─ variations.py         Auto Variations（8/16案を小さくレンダー）＋ 結果の適用（v0.3）
│   ├─ shot_maker.py         コンセプトショットメーカー（目線の高さ・レンズ・画面比率・ショットプリセット・地平線ガイド・クリックでピント）
│   ├─ photo_match.py        写真とのカメラ合わせ（フォトバッシュ向け。消失点からレンズとカメラの向き・位置を求める）
│   ├─ read_views.py         Read Views（3Dビューへの GPU オーバーレイ：グレースケール/シルエット/三分割/ぼかし）
│   ├─ ui.py                 サイドバーのパネル
│   └─ translations.py       日本語翻訳（英語が基本）
├─ tools/                    開発用スクリプト（アドオンには含めない）
│   ├─ i18n_check.py         翻訳の抜け検査（ふつうの Python で動く）
│   ├─ mock_ui_check.py      Blender 内で実行。全パネルの draw() をモック layout で呼び、存在しないプロパティ/演算子/アイコンを検出
│   ├─ render_contact_sheet.py  Blender 内で実行。CF_Test を複数ルックで小さくレンダーして比較シートを作る
│   └─ make_test_scene.py    Blender 内で実行。CF_Test シーンとテスト用アセットを作る
├─ PROJECT_CONTEXT.md        このファイル
├─ README.md
├─ CLAUDE.md                 「まず PROJECT_CONTEXT.md を読む」の案内
└─ .gitignore
```

- アドオンのパッケージ名は `conceptflow`（`ADDON_ID = __package__`）。**フォルダ名を変えない。**
- 現時点では `blender_manifest.toml`（拡張機能形式）は**未作成**。販売前に作る（第10章）。
- ライセンス: ソースのヘッダーに `SPDX-License-Identifier: GPL-3.0-or-later` と書いてあるが、**リポジトリに LICENSE ファイルはまだ無い**。販売方法（Blender 拡張機能プラットフォームは GPL 互換が必要）とあわせて小坂さんと決める（第10章）。

---

## 4. アーキテクチャ

### 4.1 登録（`__init__.py`）
`_MODULES = (translations, props, state, graybox, graybox_tools, building_extras, branch_tree, look_presets, asset_swap, variations, read_views, shot_maker, photo_match, ui)` の順に `register()` し、途中で失敗したら登録済みを逆順で `unregister()` してから例外を投げる。新しいモジュールを足すときは `_MODULES` に追加する（依存先より後ろ、`ui` の前）。

### 4.2 共通（`common.py`）
- `iface / tip / rpt`: 実行時に組み立てる UI 文字・ツールチップ・レポートの翻訳。
- `icon(name, fallback)`: 存在するアイコン名だけ返す。
- `guarded`: `execute` の最後の砦。トレースバックを `common.last_error` に保存（サポート用）。
- `ensure_child_collection(parent, name, role)`: `cf_role` カスタムプロパティで種別を付けたコレクションを作る/探す。
- `select_only`（`view_layer.objects` を走査、None をスキップ、`select_set(view_layer=...)`）、`remove_object`、`bm_box`、`mesh_from_bm`、`frame_of`、`bounds_in`。

### 4.3 Branch Tree の「状態プロバイダー」（`state.py`）
状態 = JSON → zlib → base64（先頭 `"z1:"`）。ノードの `data` 文字列に入れる。保存対象は軽量化のため限定:

| key | order | 内容 | 所有モジュール |
|---|---|---|---|
| world | 10 | シーンのワールド名・色・ノードの未接続入力値 | state.py |
| camera | 20 | カメラの行列・カメラデータ（レンズなど） | state.py |
| lights | 30 | ライトの行列・データ・表示状態（保存後に増えたライトは復元時に隠す） | state.py |
| view | 40 | カラーマネジメント（view_transform / look / exposure / gamma）、オブジェクト/コレクションの表示状態 | state.py |
| xforms | 60 | オブジェクトの `matrix_basis`（`save_transforms` が ON のとき） | state.py |
| look | 90 | Look Presets の記録（active、元のワールド、元のCM、保存した自分のライト、強さ、霧、既存ライト扱い） | look_presets.py |
| swap | 95 | 表示（Graybox/Final）とシード | asset_swap.py |

- `register_provider(key, capture(scene), restore(scene, data, report), order)` で追加できる。新しい機能の状態は**プロバイダーとして足す**。
- `equal(present, saved)` は「保存状態と今の状態が同じか」を比べる。保存後に増えたオブジェクト/非表示の新ライトは差とみなさない（`_LENIENT`）。`has_unsaved_changes()` が使う。
- `rna_dump / rna_load`: 単純な RNA プロパティ（bool/int/float/enum と Object ポインタ）を辞書に出し入れする。
- 復元で消えたオブジェクトは黙ってスキップ（開口部で分割された壁など）。カメラ/ライト/ワールドの欠落だけ報告する。

### 4.4 データの置き場所（`props.py`）
- `Scene.conceptflow` = `tree`（ノード列・index・current_uid・next_uid・save_transforms）/ `look`（Look Presets の設定と元の値）/ `swap`（部位ごとのスロット・seed・display・show_options）。
- `Object.conceptflow` = `part`（WALL/PILLAR/STAIRS/DOOR/WINDOW/FLOOR/BLOCK/NONE）、`piece`、`building`、`size`、`sill`。
- ノードの `origin` は `MANUAL` / `AUTO` / **`APPLY`（"Before Apply"。v0.3 の Compare & Apply 用に予約済み）**。

### 4.5 翻訳（`translations.py`）
`JA` タプルの列 `(English, 日本語)`。コンテキストは `"*"` と `"Operator"`。`{name}` などのプレースホルダーはそのまま残す。`bpy.app.translations.register(__package__, ...)`。
検査: `python3 tools/i18n_check.py conceptflow` → 現在 約420 文字列（検査で抜け0）（未使用の翻訳は動的ラベル用）（未使用: "Save Original" のみ）。

---

## 5. 実装済みの機能（v0.2.0）

### 5.1 Graybox（グレーBOX）— v0.1
- 演算子: `conceptflow.add_part`（壁/柱/階段/床/ドア/窓/ブロックを3Dカーソルに追加。アクティブな壁があればドア/窓を差し込んで開口部を切る）、`build_building`（部屋/建物を一括生成: 幅・奥行・階数・階高・壁厚・スラブ厚・ドア数/サイズ・窓の有無/間隔/サイズ/腰高・屋根・コーナー柱・開放面）、`cut_openings`、`merge_walls`。
- パーツは bmesh で作る箱ベースのメッシュ。`obj.conceptflow.part/piece/building/size/sill` で種別を記録。開口部を切ると壁が SOLID / SILL / HEADER の部位に分割される。
- 建物の根は空オブジェクト `CF_Building`、コレクション `CF_Graybox/CF_Building`、マテリアル `CF_Graybox_*`。
- 前面を開放した建物では、ドアは背面の壁に移り、中央に置かれる。
- 検証済み: 最大 1247 パーツの建物を 436 ms で生成、マニフォールド検査。

### 5.2 Branch Tree（分岐ツリー）— v0.1
- 状態を保存し、どこからでも分岐できる。最初の保存は必ず **"Original"**。`save_transforms` オプションで大きなシーンを軽くできる。
- 演算子: `tree_save`（名前は空なら自動）/ `tree_restore`（uid 指定または選択行）/ `tree_overwrite` / `tree_delete`（子は親につなぎ替え）/ `tree_clear`。UIList `CONCEPTFLOW_UL_tree` は階層順（`display_order`）で表示。
- API: `save_state`、`ensure_original(scene)`（ツリーが空なら Original を保存。保存したら True）、`checkpoint(scene, name, origin)`（ツリーが空なら Original、未保存の変更があれば自動ノードを保存。保存した uid か -1 を返す）、`restore_uid`、`has_unsaved_changes`、`capture_scene`。
- 検証済み: 保存/復元 5〜6 ms。ルック・差し替えの状態も一緒に保存/復元される（第6章）。

### 5.3 Look Presets（非破壊ライティング）— v0.2
HORROR / NOIR / MOONLIGHT / GOLDEN HOUR / NEON の5種を1クリックで適用。

- **非破壊**: ルックのライトは `CF_Look/CF_Look_<KEY>` コレクションに作る。ワールドは元を複製して `CF_World_<Name>`（fake user 付き）として差し替え、元のワールドは編集しない。カラーマネジメントは AgX のルックで設定。
- ワールドはノードで作るグラデーションの空（天頂/地平/地面）。霧は Volume Scatter ノード（`cf_fog` タグ付き）。
- ライトは**カメラ基準**で置く: カメラから `scene.ray_cast` で最初の面（注視点）を探し、その点からの距離比で配置（前後=forward、左右=right、上下=up）。太陽は方位 `az`（0=カメラ側、180=被写体の奥側）と仰角 `el`。
- 太陽は**閉じた部屋には入らない**ため、太陽を使うルック（HORROR/MOONLIGHT/GOLDEN HOUR）には、窓明かり役の柔らかい面光源 `window` を追加してある（2026-10-08 の調整）。これで室内でも屋外でも雰囲気が出る。
- 「Your Lights」（既存ライト）: Keep / Dim（×0.15）/ Hide（既定は Hide）。元の値を `look.saved_lights` に保存し、リセットで元に戻す。
- 強さ（Light Intensity）、霧（Haze）のオン/オフと量を後から調整可能（`on_setting_changed`）。
- 最初のルック適用時に、ツリーが空なら**現在のシーンを Original として自動保存**（`ensure_original`）。
- 演算子: `look_apply`（`preset`）、`look_reset`（元のワールド・ライト・CMに完全に戻す）、`look_remove_data`（確認ダイアログ付きで、ルックのライトとワールドを削除）。
- ルックの定義は `look_presets.PRESETS`（`sky` / `fog` / `cm` / `lights`）。`lights` の各要素は `role, type, color, bright, pos or az/el, size, spot, blend`。エネルギー換算は `_energy()`（点光源 = bright×4π d²、面 = bright×π d²、太陽 = bright）。
- 検証済み（CF_Test、閉じた部屋）: 全ルックの適用→リセットで world/CM/ライトが完全に元通り。Keep/Dim/Hide、強さ×2、霧のオン/オフ/量が効く。ツリー保存→別ルック→復元で状態が一致。

### 5.4 Asset Swap（アセット差し替え）— v0.2
- 部位タイプごとにアセットのコレクションを登録（`swap_register`: 選択オブジェクトを非表示の `CF_Assets/CF_Assets_<Part>` へ移動、ビューレイヤーから除外してスロットに設定）。コレクション直下の各オブジェクト（子ごと）または子コレクションが「候補」1つ。
- `swap_assets`（`scope` = ALL / SELECTED）: 部位ごとに**シードで決まる候補**を選び、箱に合わせてフィット（AUTO: 壁・床は TILE、ブロックは UNIFORM、他は STRETCH／回転 0/90/180/270）。メッシュを共有するリンク複製（コレクション候補はコレクションインスタンス）で軽量。差し替え後オブジェクトはグレーBOXパーツの子にして `CF_Final` コレクションに入れる（`cf_final` タグ）。
- `swap_shuffle`（seed+1 して差し替え直し）、`swap_clear`（ALL は表示も Graybox に戻し、**親のグレーBOXを消した後に残った差し替え後オブジェクトも掃除**）、表示切替 Graybox / Final（`swap.display` の update コールバックで非表示を切り替え）。
- アセットの約束: **-Y を正面、長さは X 方向**（UI に表示）。`MAX_TILES = 400` で巨大な床の分割数を制限。
- 検証済み: 530 パーツの建物で差し替え 0.37 s（1605 インスタンス）、シャッフル 1.3 s、表示切替 0.23 s、解除 0.86 s。アセットなしの警告、SELECTED の範囲、ツリーとの保存/復元も確認。

### 5.5 UI（`ui.py`）
タブ「ConceptFlow」に: **Graybox**（建物を作る / パーツ追加ボタン / 開口部 / アクティブオブジェクトの種類。子パネル **Asset Swap**）、**Branch Tree**（保存・移動・リスト・上書き/削除/全消去・メモ・現在のノード・transform オプション）、**Look Presets**（5つのルックと Reset のグリッド、強さ・霧・Your Lights、現在のルック、Remove Look Data）。
モック layout による検査（`tools/mock_ui_check.py`）で問題 0 件（`operator_menu_enum` を含む）。

---

## 6. テストの状況

### 確認済み
- v0.1: 建物生成（〜1247 パーツ 436 ms）、マニフォールド検査、ツリー保存/復元（5〜6 ms）、モックUI検査 0 件。
- v0.2: 上記 5.3 / 5.4 の検証、モックUI検査 0 件、翻訳検査（236/234）。
- ルックの見た目を CF_Test の部屋で EEVEE レンダーして目視確認（HORROR=冷たい緑＋足元のオレンジ、NOIR=モノクロの光だまり、MOONLIGHT=青い夜＋暖色の実用光、GOLDEN HOUR=暖色の斜光、NEON=マゼンタ＋シアン）。

### 未確認・要注意
- **日本語UIの実際の見え方**（翻訳の抜けは検査済みだが、Blender の言語を日本語にした状態では未確認）。
- Blender 4.2 LTS など 4.5 以外での動作。EEVEE Legacy。Windows 以外。
- Ctrl+Z（Undo）を実際に操作して戻るかの手動確認。
- ルックを**屋外/開いたシーン**で使った見た目（テストは閉じた部屋のみ）。
- 大きなユーザーシーン、リンクデータ/ライブラリオーバーライドを含むシーン、複数シーン運用。
- .blend を保存→開き直したあとのツリー復元（プロパティ自体は .blend に保存される設計だが、実機で未確認）。
- 自動テスト（pytest など）は**まだ無い**。過去の「bpy をスタブにしたオフラインテスト」は使い捨てで、リポジトリには入っていない（`state.equal()` などは純粋な Python なのでテストを足しやすい）。

---

## 7. 未実装・これからやること

### 7.1 v0.3（MVP 完成の最後のバージョン）
1. **Auto Variations**（8パターン自動生成）— 実装済み・実機確認待ち
2. **Compare & Apply**（比較して適用）— 基本版は実装済み・実機確認待ち（大きいグリッド表示などは実機を見て調整）
3. **Read Views**（絵の確認ビュー）
4. 仕上げ: 全体の動作確認、翻訳の抜け確認、モックUI検査、ハッシュ確認（旧運用）、ボード更新、小坂さんへの報告と確認。

### 7.2 MVP 完成後
- 「アドオンとして常に有効にしますか？」と質問 → 了承なら永続的に有効化。
- テスト用データ（CF_Test など）を残すか消すか質問。
- 販売パッケージ（第10章）を相談して作る。

### 7.3 MVP に含めないもの（後回し）
Reference Board、Composition Solver、進化型生成、Change Detector、カスタムプリセット、Concept Board。

---

## 8. v0.3 の設計メモ（**決定案。コードはまだ無い**）

小坂さんの指示（コンセプトシート v2）:
- **Auto Variations**: アングル・レンズ・時間帯・空気感を振る。**単独モード**（1要素ずつ変えて8枚）と**組み合わせモード**（掛け合わせ16枚）。EEVEE・480px で高速レンダー、コンタクトシート PNG で書き出し。
- **Compare & Apply**: サムネイルをグリッドで並べ、クリックした案をシーンに反映。**適用前の状態は自動で Branch Tree に残る**（いつでも戻せる）。
- **Read Views**: グレースケール／シルエット／三分割／ぼかしをワンタッチで切り替え。点数は付けない。**「Off」ボタンを付ける**（合意済み）。
- レンダーの保存先: **.blend の隣の `ConceptFlow_Renders` フォルダ**（未保存なら一時フォルダ）（合意済み）。

### 8.1 Auto Variations（案）
- 新規 `variations.py`。軸は **Angle / Lens / Time of day / Atmosphere（空気感）**。
- 単独モード: 軸を1つ選び 8 枚。組み合わせモード: 2軸以上を選び 16 枚（2軸なら 4×4 の全組み合わせ、3軸以上は各軸4値のシード付きラテン方格サンプリング）。
- 1案 = **ベース状態に対するパラメーターの辞書（JSON）**。全状態は持たない。適用 = ベースを復元してから差分を適用。
  - Angle: 注視点（`look_presets.find_target`）まわりに方位/仰角/距離比で公転。壁にぶつかる場合は `scene.ray_cast` で手前に詰める（詰めすぎる案は除外）。シードで少し揺らす。
  - Lens: 焦点距離（例 14〜120mm）。被写体の大きさを保つようドリー（距離 ∝ 焦点距離）。
  - Time of day: 時刻から太陽の仰角・方位・色温度・空の色・窓明かり・露出を決める**カスタムルック**として適用。`look_presets.apply_look` が仕様辞書を受け取れるよう拡張し（`LOOK_ITEMS` に非表示の CUSTOM を追加、仕様 JSON を `look` のプロパティに保持）、Branch Tree・強さ・霧の調整がそのまま動くようにする。
  - Atmosphere: 霧の濃さ/色（Clear〜Heavy fog、Dust、Smoke など）。ルックが有効ならその仕様を複製して霧だけ上書き。ルックなしなら、元ワールドの複製＋霧ノードだけのカスタムルック。
- レンダー: EEVEE（`BLENDER_EEVEE_NEXT`）、幅 480px、低サンプル。`bpy.ops.render.render(write_still=True)` で一時 PNG に書き、numpy でコンタクトシート化。ユーザーのシーンを一時的に変えるので、**`state.capture` でベースを保存 → 各案を適用してレンダー → `finally` で `state.restore` → `state.equal` で戻りを確認**する。レンダー設定（解像度・エンジン・ファイルパス）も保存/復元。
- 実行方式: 1案ずつ進む Python ジェネレーターを作り、モーダル演算子（タイマー）で動かす。ESC で中止（ベースを復元）。モーダル中は他のイベントを食う（途中でシーンを触らせない）。テストではジェネレーターを直接回す。
- 出力: `ConceptFlow_Renders/<blend名>_<日時>/vNN.png` と `contact_sheet.png`。コンタクトシートに文字ラベルを入れる方法（blf のオフスクリーン描画など）は未決。
- 新しいプロパティ: `Scene.conceptflow.variations`（モード、軸、組み合わせ用の軸チェック、シード、結果のリスト＝ラベル・仕様JSON・サムネイルパス、ベース状態など）。

### 8.2 Compare & Apply（案）
- サムネイルは `bpy.utils.previews` で読み込む（歪まないよう正方形 256px に余白付きで収める）。
- **クリックで適用**の UI 手段は要検証: (a) `layout.operator(..., icon_value=...)` のボタンが `scale_y` で拡大表示されるか、(b) 使えなければ `template_icon_view` のポップアップグリッド、(c) 最後の手段は `template_icon` ＋ 下に適用ボタン。画面確認が必要。
- 適用の手順: `branch_tree.checkpoint(scene, name, origin='APPLY')` で適用前の状態を保存 → ベースを復元 → 案を適用 → 適用後の状態もノードとして保存（例 "Var 03: …"）。「ベースに戻す」ボタンも付ける。

### 8.3 Read Views（案）
- 新規 `read_views.py`。Grayscale / Silhouette / Thirds / Blur / **Off**。シーンのデータを恒久的に変えないこと、点数を付けないこと。
- Thirds は `camera.data.show_composition_thirds`（元の値を保存して Off で戻す）。
- Grayscale / Silhouette（輝度のしきい値で2値化）/ Blur（ぼかし）は、**3Dビューの POST_PIXEL 描画ハンドラ＋`gpu.types.GPUOffScreen.draw_view3d` で描いた画像をシェーダーで加工して重ねる**案が本命。4.5 の EEVEE Next で動くか・速度は**未検証**。
- フォールバック: Grayscale は `scene.view_settings.look = 'AgX - Greyscale'`（この場合 Branch Tree の `view` プロバイダーに取り込まれないよう除外する必要がある）、Silhouette はソリッド表示のフラット黒＋白背景。
- Blender 4.5 でビューポートコンポジターを使う案は、`scene.node_tree`（ユーザーのコンポジット）を触ってしまうため**避ける**。

### 8.4 小坂さんに確認が必要な点
- Read Views の方式（GPU オーバーレイ案 ⇔ シーン設定を使う簡易案）の許容範囲。
- 時間帯の軸が Look Presets を置き換える（アクティブなルックが CUSTOM になる）ことの了承。
- コンタクトシートの文字ラベルの要否。

---

### 8.5 実装メモ（2026-10-09、`variations.py`）
設計案からの変更・決定:
- 1案 = `{"label", "cam": {yaw, elev, dist, lens}|None, "time": 時刻|None, "atmos": 番号|None}`。軸の値は `ANGLES / LENSES / HOURS / ATMOSPHERES`（組み合わせは `[::2]` の4値）。単独=8案、組み合わせ=16案（2軸は全組み合わせ、3軸以上はシード付きの散らし方）。
- 時間帯・空気感は **`look_presets` のカスタムルック**（`LOOK_ITEMS` に `CUSTOM` を追加、仕様は `look.custom_spec` にJSON）。`apply_look(scene, 'CUSTOM', context, spec=..., view=...)`。仕様に `sky: None` なら元のワールドを複製して霧だけ足す（`CF_World_CustomHaze`）、`keep_lights: True` なら既存ライトに触らない。Branch Tree の `look` プロバイダーに `custom` を追加済み。
- 注意（既知の制限）: カスタムルックのワールド（空のノード）は共有なので、古い Branch Tree ノードを復元しても空の見た目は最後に作った案のものになる（ライト・カメラ・露出は復元される）。
- 生成は **モーダル演算子**（タイマー、Esc で中止）。ベース状態を `state.capture` → 各案ごとに `restore` ＋ 適用 → `bpy.ops.render.render(write_still=True)` → 最後に必ず復元（ジェネレーターの `finally`）し、`state.equal` で戻りを確認。レンダー設定（エンジン・解像度・パス・形式・サンプル）も保存/復元。EEVEE・幅480px・16サンプル。
- 出力: `.blend` の隣の `ConceptFlow_Renders/<blend名>_<日時>/vNN.png` ＋ `contact_sheet.png`（numpy。**文字ラベルは未実装**。ラベルはパネルのボタン名に表示）。未保存の .blend は一時フォルダ。
- 適用（基本の Compare & Apply）: パネルに `template_icon`（プレビュー画像）＋ボタンを2列で並べる。適用 = `branch_tree.checkpoint(..., 'APPLY')`（"Before Apply"）→ ベースを復元 → 案を適用 → "Var NN: ラベル" として保存。「ベースに戻す」「フォルダを開く」「結果を消去」付き。
- **Blender 実機で要確認**: (1) モーダル中の `render.render` が動くか・速度、(2) 生成後にシーンが完全に元に戻るか（警告が出ないか）、(3) パネルのサムネイル表示（`template_icon` の見え方・大きさ）、(4) コンタクトシートの向き・色（`image.pixels` の扱い）、(5) 各軸の見た目（壁に近いときのカメラ詰め、広角のドリー量、時間帯ごとの明るさ）、(6) 適用→Branch Tree で戻せるか、(7) 日本語UI、(8) `tools/mock_ui_check.py`（新パネル）。

### 8.6 Read Views の実装メモ（2026-10-09、`read_views.py`、GPUオーバーレイ方式で決定）
- `SpaceView3D.draw_handler_add(..., 'WINDOW', 'POST_PIXEL')`。Grayscale / Silhouette / Blur は `GPUOffScreen.draw_view3d`（現在のビューの view/window 行列）で描いた画像をシェーダー（`GPUShaderCreateInfo`）で加工して全面に重ねる。Blur は半分の解像度で描いて7×7ガウス。Silhouette は輝度のしきい値（既定0.35、スライダー）。Thirds はカメラ枠（カメラビュー時）または画面全体にガイド線を描く。
- **シーンのデータは一切変えない**（カラーマネジメントやカメラ設定も触らない）。設定は `SKIP_SAVE`（.blend に保存しない）。点数は付けない。パネルに Off ボタン。描画で例外が出たらオーバーレイを止め、パネルに警告（`last_error` に保存）。
- 未使用時はハンドラーを外す（`on_changed`）。アドオン無効化時もオフスクリーンを解放。
- **実機で要確認**: シェーダーが 4.5 で通るか、オフスクリーン描画の速度（大きなシーン）、カラーの見え方（`do_color_management=True` で表示と一致するか）、カメラビューでの三分割の位置、複数の3Dビューでの動作、ギズモ/オーバーレイとの重なり、日本語UI、`tools/mock_ui_check.py`。

### 8.7 2026-10-12 の見直し（小坂さんの実機確認を受けて）
実機確認の結果（予約タスク「ConceptFlow MVP開発」のチャット経由）: 「まだ商品として出せない」。BuildingTool はもう少し、Look Presets は今のレベルなら不要、Auto Variations は詰めれば使える、Read Views は保留。選ばれた方針: GitHub 側の版を土台に、**Look Presets は作り直して強化**、建物ツールは「クリックで壁を引く／階段・屋根・バルコニー／家具・小物／街並み」を追加、Variations は「比較して適用する操作」と「狭い部屋でも使えるカメラ」を詰める。Read Views は保留（コードは残す）。

実機で分かった重要な事実（EEVEE Next 4.5.4）: **ワールドの Volume Scatter（霧）を入れると、密度 1e-4 以上で太陽光が消え画面がほぼ真っ黒になる**（点光源は効く）。**箱型のボリュームオブジェクト（密度 0.01、60m 立方）なら太陽も効く。**
この日のコード変更（すべて実機未検証）:
- `look_presets.py`: 霧をワールドから **箱のボリュームオブジェクト `CF_Haze_<KEY>`**（シーンの範囲＋カメラを含む、最大60m、`cf_look_role="haze"`、マテリアル `CF_Haze_<KEY>`）に変更。`_update_haze` が表示/密度/範囲を更新。旧版のワールド内霧ノード（`cf_fog`）は適用時に削除。`find_target` と Variations のレイキャストは look オブジェクト（霧の箱）を無視。`remove_look_data` は霧の箱とマテリアルも消す。**プリセットの中身（ライトの数値）は未調整** — 太陽が効くようになった分、明るさが変わるので実機で見て調整が必要。
- `variations.py`: 狭い部屋用カメラ `find_camera_spot`（目標位置から壁までのレイキャストで空きを調べ、向きを回し・距離を詰め・仰角を下げて空いた位置を探す。疑似の部屋でのテストでは全案が部屋の中に収まった）。適用した案を強調表示、「適用を戻す」（Before Apply のノードへ）、列数スライダー、ボタンに連番。
- `graybox.py`: `build_building` を関数に切り出し。屋根の形（フラット／片流れ／切妻、勾配、軒の出、妻壁）、バルコニー（手すり付き、上階のドア）。`new_custom_part`、`prism_mesh` を追加。
- `graybox_tools.py`（新規）: **壁を描く**（モーダル。クリックで点、Shift=15°、Ctrl=スナップなし、C=閉じる、Enter/右クリック=完了、Backspace=戻す、Esc=取り消し、閉じたら床スラブ）、**小物13種**（テーブル・いす・ベッド・ソファ・棚・カウンター・ベンチ・樽・木箱・柵・街灯・木・車。BLOCKとして作るので差し替え可）、**階段の種類**（まっすぐ／L字／U字／らせん）、**街並み**（形の違う建物の列、両側、道路）。
- 実機で要確認: 霧の箱で太陽が効くか・各ルックの見た目、壁を描く操作（座標・スナップ・プレビュー線）、屋根/バルコニーの位置、小物の見た目、街並みの配置（向き・道路）、らせん階段、狭い部屋でのカメラ、適用を戻す、日本語UI、`tools/mock_ui_check.py`。

### 8.8 コンセプトショットメーカー（2026-10-12、`shot_maker.py`。ボードの「cf-shot」案を実装）
- パネル「Shot Maker」。**ビューからカメラ**（なければ CF_Cam を作成）、**ショット9種**（全景・目の高さ・ヒーロー・寄り・室内広角・俯瞰・ドローン・虫の目・ダッチ。被写体＝カメラが見ている最初の面のまわりに、`variations.find_camera_spot` で壁を避けて配置。適用前に Branch Tree へ自動保存）、**目線の高さ**（虫の目0.15m／子ども1.0／大人1.6／バルコニー5／ドローン30 とスライダー。「狙いを保つ」で同じ被写体を見続ける）、**レンズ**（スライダー＋14/24/35/50/85/135。「ドリー」で被写体の大きさを保つ）、傾き（ロール）、シフトY、**画面の比率**（16:9・2.39:1・1.85:1・3:2・4:3・1:1・9:16。長辺を保つ）、**クリックでピント**（モーダル。被写界深度オン、フォーカス距離を設定）、絞り（F値）、**地平線ガイド**（カメラビューに地平線とカメラの高さを重ねる。`SKIP_SAVE`）。
- スライダーはシーンのカメラそのものを読み書きする get/set プロパティ。ロール符号・各ショットの位置はオフラインの計算テストで確認（狙いのずれ0、ロール14°が読み戻し14°）。
- 実機で要確認: パネルの並び、クリックでピントの座標、地平線ガイドの位置（カメラ枠）、ショットの見た目、画面比率の変更、日本語UI。

### 8.9 写真とのカメラ合わせ（2026-10-13、`photo_match.py`。ボードの「photo-camera」案を実装）
- パネル「Photo Match」。**写真を読み込む**（カメラの背景画像に設定、タグ `cf_photo`。レンダーの画面の形を写真の縦横比に合わせる）→ **カメラを合わせる**（モーダル。カメラビューで、赤2本・緑2本の線を各2点クリックで引き、最後に地面の原点をクリック。Backspace で戻す、Esc で取り消し）→ 消失点2つからレンズ・向き・位置を計算してカメラに設定（適用前のカメラは Branch Tree に自動保存）。
- 計算（fSpy と同じ考え方）: 画像中心を光学中心とみなし、f² = −(v1·v2)。2つの消失点方向が X・Y 軸、外積が Z（上）。X は画面の右向きに取り、「向きを反転」で 180° 回せる。原点の点を地面（z=0）の点とし、**カメラの高さ**（既定 1.6m）でスケールを決める。
- オフラインの合成テスト（既知のカメラの線から復元）で、焦点距離は誤差 0.002px 程度で復元、向きと位置も再投影で一致（消失点が遠い／原点が地平線に近いなど悪条件では誤差が大きくなる）。
- 制限: 光学中心は画像中心と仮定（トリミングした写真は不可）、歪み補正なし、光学的に水平方向の2方向（X・Y）のみ。レンズシフトは 0 にする。
- 実機で要確認: 背景画像の表示（FIT・前面）、線を引く位置と写真の画素の対応（カメラ枠の計算）、レンズの値（センサー幅 36mm、AUTO）、カメラビューのズーム／パン中の動作、日本語UI。

### 8.10 ビルディングツール一式（2026-10-13、ボードの「ビルディングツール」仕様を実装）
仕様→実装の対応（すべて実機未検証。オフラインの計算テストのみ）:
- **壁を引く**: `graybox_tools.draw_walls`（モーダル）。壁＋閉じたら**床と天井**（多角形スラブ、`cf_poly` に輪郭を保存）。終了時に**ドア・窓を自動配置**（スタイルの値。一番長い壁にドア）。**壁をクリックしてドア／窓を足す**: `building_extras.click_opening`（壁を切る。続けてクリック可）。
- **下絵をなぞる**: `underlay_load`（画像を画像エンプティとして地面に敷く、`cf_underlay`）、実寸・不透明度、**2点クリックでスケール合わせ**（既知の長さ）、削除。そのまま「壁を描く」でなぞる。※画像エンプティが XY 平面に敷かれる前提（実機で要確認）。
- **空間スタイル**（`building_extras.STYLES`）: 現代の部屋／和室／廃墟／SF廊下／石造りのホール／倉庫／店舗。壁の高さ・厚み・窓の並び・ドア・屋根を一括設定（壁を描く・建物を作る・街並みが使う）。廃墟スタイルは廃墟化の量も0.55にする。
- **廃墟化スライダー**: `ruin`（量・シード）。壁を列に分けて高さを欠かし、穴を開け、崩れた部分を作る。床・屋根スラブはタイルを抜く。選んだもの（なければ全部）が対象。Ctrl+Z で戻る。
- **階段・屋根・バルコニー**: 直階段／L字／U字（踊り場付き）／らせん、屋根はフラット／片流れ／切妻／**寄棟／ドーム**、バルコニー、**庇**、**アーチの壁**、単体の寄棟・ドーム。
- **家具・小物キット**: 13＋噴水＝14種。**部屋に自動で散らす**（`scatter_props`。キット3種、密度・シード、壁際は背を壁に向ける、ドア前は避ける、重ならない。ポリゴン床にも対応）。**Asset Swap 連動**: 小物ブロックは `cf_prop` を持ち、差し替えのとき名前に種類（table など）を含む候補を優先する。
- **街並み**: 通り／**路地**（細い道・奥行きふぞろい・1階多い）／**広場**（四方の建物が噴水に向く・街灯）。高さ・幅ランダム、屋根もランダム（寄棟含む）、スタイル対応。
- **スケール人物**: 1.7m（大人）／1.2m（子ども）／1.95m（背高）／任意の身長。ブロックパーツではないので差し替えの対象外。
- 実機で要確認: 壁を描く＋自動ドア窓の位置、下絵の向きとサイズ、廃墟化の見た目と重さ（パーツ数の上限500/壁）、家具の配置、広場・路地の配置と向き、寄棟屋根の形、日本語UI。

## 9. 既知の問題・注意点（ハマりどころ）

### Blender API
- `Object.select_set / select_get` は `view_layer` 引数を付ける。同じ演算子内でオブジェクトを削除すると `view_layer.objects` に None が混ざるので、`select_only` は None を飛ばす。
- 太陽ライトは閉じた部屋の中に届かない。室内でも効かせたいルックは `window` 役の面光源を併用する（5.3）。
- Blender の画像ピクセルは**下の行から**始まる。numpy でタイルを並べるときは行順を逆にする（`tools/render_contact_sheet.py` は対応済み）。
- 起動し直しで未保存のデータ（CF_Test など）は消える。2026-10-08 に実際に起きた。CF_Test は `tools/make_test_scene.py` で作り直せる。
- アドオンを `default_set=False` で有効にしただけでは、Blender を再起動すると無効に戻る。

### 旧開発環境の癖（Cowork セッション＋ MCP for Blender）
- `execute_blender_code` は `user_prompt`（ユーザーの言葉そのまま）を必須とする。`look(image=...)` で PNG を確認できる。
- `device_commit_files` で書いたファイルが**古い版になることが何度かあった**（common.py 2回、state.py 1回、look_presets.py・asset_swap.py 各1回）。コミットのたびに Blender 側で各ファイルの sha256 を計算し、クラウド側の `sha256sum` と比べ、違えば少し待って再コミットする。
- 開発中の再読み込み: `addon_utils.disable("conceptflow", default_set=False)` → `sys.modules` から `conceptflow` で始まるものをすべて削除 → `addon_utils.enable("conceptflow", default_set=False)`。

### 設計上の注意
- `look` の元の値（元のワールド・CM・自分のライト）は最初のルック適用時に保存し、リセットで復元する。ルックのライト/ワールドはリセット後も**隠したまま残す**（Branch Tree の保存状態が使うため）。完全に消すのは `look_remove_data`。
- Branch Tree の復元後は、保存状態に入っていない「Look の既存ライト設定（existing）」も保存値に戻る（Look の設定は状態の一部）。
- `bl_info` の `blender` は `(4, 2, 0)` だが、4.5.4 でしか検証していない。

---

## 10. 開発・配布のやり方

### 10.1 Blender に入れて試す
1. `conceptflow/` フォルダを Blender のアドオンフォルダにコピーする。Windows の例:
   `C:\Users\<ユーザー名>\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons\conceptflow\`
2. Blender の Preferences > Add-ons で「ConceptFlow」を有効にする（または下のスクリプト）。3Dビューで N キー → タブ「ConceptFlow」。
3. 再読み込み（開発中）:
   ```python
   import sys, addon_utils
   addon_utils.disable("conceptflow", default_set=False)
   for k in [k for k in sys.modules if k == "conceptflow" or k.startswith("conceptflow.")]:
       del sys.modules[k]
   addon_utils.enable("conceptflow", default_set=False)
   ```
4. テスト用シーン: Blender のテキストエディターで `tools/make_test_scene.py` を実行 → `CF_Test` ができる（部屋＋カメラ＋太陽＋テスト用アセット）。比較シート: `tools/render_contact_sheet.py`。UI 検査: `tools/mock_ui_check.py`。

### 10.2 クラウドの Claude Code セッションで続きを開発するとき
- クラウドのセッションには **Blender が無く、小坂さんの Blender を操作する MCP も無い**。できること: コードの設計・実装、`tools/i18n_check.py`、`python -m py_compile`、`bpy` をスタブにした純粋ロジックのテスト（`state.equal`、`fit_matrices` など）。2026-10-08 時点で、クラウド環境から `pip install bpy` は通らなかった（Python 3.13 / 4.5 用ホイールが見つからない）ため、実際の `bpy` でのテストはできない前提で進める。
- **Blender 実機での確認が必要な変更**は、小坂さんの PC につないだ Cowork セッション（Blender MCP 付き）か、小坂さん自身の手動確認に回す。確認してほしい手順をこのファイルかプルリクエストの説明に書く。
- コミット前に毎回: `python3 -m py_compile conceptflow/*.py`、`python3 tools/i18n_check.py conceptflow`。UI 文字列を足したら翻訳も足す。
- 作業後はこのファイルを更新する。

### 10.3 販売に向けて（MVP 完成後に小坂さんと相談）
- `blender_manifest.toml`（Blender 4.2+ の拡張機能形式。`id = "conceptflow"`、`version`、`blender_version_min`、`license`、`tagline`、`maintainer`、権限など）を作り、拡張機能 zip を作る。`bl_info` は互換のため残すか整理するか決める。
- ライセンス（GPL 互換が必要か）、価格、配布先（Blender Extensions／Gumroad／BOOTH など）、製品名/商標の確認、サンプルシーン/アセットの権利。**いずれも未決定。**
- 販売前に: 日本語UIの実機確認、4.2 LTS での動作確認、`last_error` を使ったサポート手順、README・ドキュメント・デモ動画。

---

## 11. 変更履歴

- **v0.1**（小坂さん確認済み）: Graybox ＋ Branch Tree。
- **v0.2**（2026-10-08、小坂さんへ報告済み・次へ進む確認待ちの状態で v0.3 着手の指示）: Look Presets 5種、Asset Swap。調整: GOLDEN HOUR が閉じた部屋で真っ黒になる問題を、`window` 面光源の追加で解決。HORROR / MOONLIGHT / NOIR を調整。`swap_clear` が孤立した差し替え後オブジェクトも掃除するよう修正。
- 2026-10-13: ビルディングツール一式を追加（8.10）。
- 2026-10-13: 写真とのカメラ合わせを追加（8.9）。
- 2026-10-12: コンセプトショットメーカーを追加（8.8）。
- 2026-10-12: 実機確認を受けた見直し（8.7）。霧を箱ボリュームに変更（太陽が消える問題の対策）、建物ツール拡張（壁を描く・屋根・バルコニー・小物・階段・街並み）、Variations の狭い部屋カメラと適用操作。Blender 実機では未検証。
- 2026-10-09: リポジトリ構成を整理（`conceptflow-blender-repo/` の中身を直下へ）、`.gitignore` 追加。v0.3 の **Auto Variations** と基本的な **Compare & Apply** を実装（`variations.py`、`look_presets` のカスタムルック対応、UIパネル、翻訳）。Blender 実機では未検証（8.5 の確認リスト）。Read Views は未着手。
- 2026-10-08: v0.3 の設計メモを作成（第8章）。GitHub 保存用の整理（このファイル、README、`tools/`、`.gitignore`、`CLAUDE.md` を追加。**`conceptflow/` 内のコードは変更していない**）。

### 直近の動作確認時のファイルハッシュ（sha256 の先頭12桁）
Blender 側にデプロイ済みの版と一致を確認したもの。リポジトリとの食い違い検出に使える。

```
36f757d47c52  __init__.py
9a31e704dff9  asset_swap.py
7b269930c780  branch_tree.py
079f6ce63c3e  common.py
c88b9a4a66aa  graybox.py
f5e4f8cc6f1b  look_presets.py
9ccbdda8f0d8  props.py
7bacbfd595a8  state.py
31f8ca0e26d2  translations.py
14f102ad5e99  ui.py
```
