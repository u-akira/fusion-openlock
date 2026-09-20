# Fusion 360 OpenLOCK Hole MVP

Fusion 360用のPythonアドインです。`Create OpenLOCK Hole` コマンドで、
ユーザー提供の寸法図を読み取った `Basic OpenLOCK` プロファイルを
編集中のスケッチへ追加します。コマンドは Sketch コンテキストでのみ表示されます。

## MVPでできること

- 編集中のスケッチへの輪郭追加
- スケッチ線を1本選び、13.8 mm上辺を同一直線に拘束（作成時は中点同士を合わせる）
- 基準線選択後の輪郭プレビューと、`Flip` による向きの切り替え
- 基準線を軸にした反転
- 中央スロットの4隅にR0.50 mmのスケッチフィレットを作成
- 生成後は線長・角度・フィレット半径・接線拘束で形を保つ（頂点は固定しない）
- 13.8 mm上辺と基準線の同一直線拘束により、基準線から外れない配置を維持
- 生成した各輪郭線・円弧へのプロファイルバージョン・配置情報の保存
- ソリッドの自動切り抜きは未実装（仕様どおりMVP対象外）

## インストール

1. `OpenLOCKHole` フォルダをFusionのAdd-Insフォルダへコピーする。Windowsでは通常 `%appdata%\\Autodesk\\Autodesk Fusion\\API\\AddIns` です。
2. Fusion 360で **Utilities > Scripts and Add-Ins** を開く。
3. **Add-Ins** タブで `OpenLOCKHole` を選択して実行する。

または、**Scripts and Add-Ins** の緑色の `+` から `OpenLOCKHole.py` またはフォルダを指定できます。

`OpenLOCKHole.py` と `OpenLOCKHole.manifest` は同名で同じフォルダに置いてください。

## 使い方

1. Designワークスペースで対象スケッチを編集する。
2. Sketchタブの `Create OpenLOCK Hole` を起動する。
3. `Reference Edge` にスケッチ線を1本選ぶ。13.8 mm上辺が選択線と同一直線になり、作成時は両辺の中点が合うプレビューが表示される。
4. 向きが逆なら `Flip` を切り替える。垂直オフセット入力や作成後の専用移動コマンドはありません。

`Reference Edge` と生成した13.8 mm上辺は同一直線拘束で関連付けられます。作成時の中点一致は初期配置であり、拘束は辺の同一直線関係を維持します。

## 寸法について

`openlock_profile.py` の寸法は、2026-09-19のユーザー提供寸法図から読み取った値です。
輪郭と寸法の対応は `SPEC.md` に記録しています。公式v5.5リファレンスシートまたは
元CAD/STLとの照合は未実施です。

## ローカルテスト

Fusion 360本体のAPIは通常のPython環境にはないため、ローカルではプロファイル計算部分のみ確認します。

```powershell
python -m unittest discover -s tests -p "test_*.py"
```
