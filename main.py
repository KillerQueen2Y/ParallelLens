import sys
import csv
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, QUrl, QTimer, Slot
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)


MAX_FOLDERS = 10


class FolderVideoPlayer:
    """Helper that bundles a player, video widget and meta for one folder."""

    def __init__(self, folder: Path, parent: QWidget | None = None) -> None:
        self.folder = folder
        # 不再使用 prompts.csv 中的文案
        # self.prompts: Dict[int, str] = {}
        # self._load_prompts()

        self.video_widget = QVideoWidget(parent)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setMinimumHeight(180)

        self.player = QMediaPlayer(parent)
        self.audio_output = QAudioOutput(parent)
        # 正常把视频输出到 QVideoWidget
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio_output)

        self.info_label = QLabel(self.folder.name, parent)
        self.info_label.setWordWrap(True)

        # 保存最近一帧，用于截图
        self.last_frame = None
        # 通过 QVideoWidget 自带的 videoSink 监听帧变化
        self.video_sink = self.video_widget.videoSink()
        if self.video_sink is not None:
            self.video_sink.videoFrameChanged.connect(self._on_frame_changed)

    @Slot(object)
    def _on_frame_changed(self, frame) -> None:
        try:
            img = frame.toImage()
        except Exception:
            return
        if img.isNull():
            return
        # 复制一份，避免后续帧复用底层缓冲导致内容变化
        self.last_frame = img.copy()

    def _load_prompts(self) -> None:
        csv_path = self.folder / "prompts.csv"
        if not csv_path.exists():
            return
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                # 允许没有表头的简单 index,prompt 结构
                for row in reader:
                    if len(row) < 2:
                        continue
                    try:
                        idx = int(row[0])
                    except ValueError:
                        continue
                    self.prompts[idx] = row[1]
        except Exception:
            # 解析失败就静默忽略，依然可以播放视频
            self.prompts = {}

    def set_video_by_name(self, file_name: str) -> bool:
        video_path = self.folder / file_name
        if not video_path.exists():
            return False

        self.player.setSource(QUrl.fromLocalFile(str(video_path)))

        # 简化展示信息：只显示文件夹名和文件名
        self.info_label.setText(f"{self.folder.name} — {file_name}")
        return True

    def set_position(self, position_ms: int) -> None:
        if self.player.source().isEmpty():
            return
        self.player.setPosition(position_ms)

    def duration(self) -> int:
        return self.player.duration() or 0

    def play(self) -> None:
        if not self.player.source().isEmpty():
            self.player.play()

    def pause(self) -> None:
        self.player.pause()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Multi-folder Synchronized Video Viewer")
        self.resize(1400, 900)

        self.folder_players: List[FolderVideoPlayer] = []
        self.master_player: QMediaPlayer | None = None
        self._is_slider_dragging = False

        self._build_ui()
        self._create_menu()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # 顶部：文件夹列表 + 控制区
        top_layout = QHBoxLayout()
        main_layout.addLayout(top_layout)

        # 左边：已选择的文件夹列表
        folder_layout = QVBoxLayout()
        top_layout.addLayout(folder_layout, 1)

        folder_label = QLabel("已选择文件夹 (最多 10 个):")
        folder_layout.addWidget(folder_label)

        self.folder_list = QListWidget()
        folder_layout.addWidget(self.folder_list)

        btn_add_folder = QPushButton("添加文件夹…")
        btn_add_folder.clicked.connect(self.on_add_folder_clicked)
        folder_layout.addWidget(btn_add_folder)

        btn_clear_folders = QPushButton("清空文件夹")
        btn_clear_folders.clicked.connect(self.on_clear_folders_clicked)
        folder_layout.addWidget(btn_clear_folders)

        # 右边：视频名输入
        control_layout = QVBoxLayout()
        top_layout.addLayout(control_layout, 2)

        name_layout = QHBoxLayout()
        control_layout.addLayout(name_layout)

        name_label = QLabel("视频文件名 (例如: video_064.mp4):")
        name_layout.addWidget(name_label)

        self.video_name_edit = QLineEdit()
        self.video_name_edit.setPlaceholderText("video_064.mp4")
        name_layout.addWidget(self.video_name_edit)

        btn_load_video = QPushButton("加载该视频到所有文件夹")
        btn_load_video.clicked.connect(self.on_load_video_clicked)
        control_layout.addWidget(btn_load_video)

        # 截图相关控件
        shot_name_layout = QHBoxLayout()
        control_layout.addLayout(shot_name_layout)

        shot_name_label = QLabel("截图名：")
        shot_name_layout.addWidget(shot_name_label)

        self.screenshot_name_edit = QLineEdit()
        self.screenshot_name_edit.setPlaceholderText("例如: good_frame")
        shot_name_layout.addWidget(self.screenshot_name_edit, 1)

        btn_capture = QPushButton("截图当前帧")
        btn_capture.clicked.connect(self.on_capture_screenshot_clicked)
        shot_name_layout.addWidget(btn_capture)

        dir_layout = QHBoxLayout()
        control_layout.addLayout(dir_layout)

        dir_label = QLabel("保存到：")
        dir_layout.addWidget(dir_label)

        self.screenshot_dir_edit = QLineEdit()
        self.screenshot_dir_edit.setPlaceholderText("请选择保存截图的文件夹")
        self.screenshot_dir_edit.setReadOnly(True)
        dir_layout.addWidget(self.screenshot_dir_edit, 1)

        btn_browse_dir = QPushButton("选择文件夹…")
        btn_browse_dir.clicked.connect(self.on_select_screenshot_dir_clicked)
        dir_layout.addWidget(btn_browse_dir)

        control_layout.addStretch(1)

        # 中间：多路视频区域（可滚动）
        self.video_grid = QGridLayout()
        self.video_grid.setContentsMargins(0, 0, 0, 0)
        self.video_grid.setHorizontalSpacing(4)
        self.video_grid.setVerticalSpacing(4)

        self.video_container = QWidget(self)
        self.video_container.setLayout(self.video_grid)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.video_container)
        main_layout.addWidget(self.scroll_area, 5)

        # 底部：播放控制 + 进度条
        bottom_layout = QHBoxLayout()
        main_layout.addLayout(bottom_layout)

        self.btn_play_pause = QPushButton("播放")
        self.btn_play_pause.setEnabled(False)
        self.btn_play_pause.clicked.connect(self.on_play_pause_clicked)
        bottom_layout.addWidget(self.btn_play_pause)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 1000)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderPressed.connect(self.on_slider_pressed)
        self.position_slider.sliderReleased.connect(self.on_slider_released)
        self.position_slider.sliderMoved.connect(self.on_slider_moved)
        bottom_layout.addWidget(self.position_slider, 1)

        self.time_label = QLabel("00:00 / 00:00")
        bottom_layout.addWidget(self.time_label)

        # 如果需要更严格的同步，可以再加定时器做小幅校正，
        # 目前先不启用，避免频繁 setPosition 造成卡顿感。

    def _create_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")

        act_add = QAction("添加文件夹…", self)
        act_add.triggered.connect(self.on_add_folder_clicked)
        file_menu.addAction(act_add)

        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    # ---------- Folder management ----------

    @Slot()
    def on_add_folder_clicked(self) -> None:
        if len(self.folder_players) >= MAX_FOLDERS:
            QMessageBox.warning(self, "提示", f"最多只能添加 {MAX_FOLDERS} 个文件夹。")
            return

        path_str = QFileDialog.getExistingDirectory(self, "选择包含 mp4 和 prompts.csv 的文件夹")
        if not path_str:
            return

        folder = Path(path_str)
        if not folder.exists():
            return

        if any(p.folder == folder for p in self.folder_players):
            QMessageBox.information(self, "提示", "该文件夹已经添加过了。")
            return

        self.add_folder(folder)

    def add_folder(self, folder: Path) -> None:
        player = FolderVideoPlayer(folder, self)
        self.folder_players.append(player)

        item = QListWidgetItem(str(folder))
        self.folder_list.addItem(item)

        # 将视频控件加到网格布局：一行最多两个，超出换行
        index = len(self.folder_players) - 1
        cols = 2
        row = index // cols
        col = index % cols

        container = QWidget(self)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.addWidget(player.video_widget)
        vbox.addWidget(player.info_label)
        container.setMinimumHeight(220)

        self.video_grid.addWidget(container, row, col)

    @Slot()
    def on_clear_folders_clicked(self) -> None:
        self.folder_players.clear()
        self.folder_list.clear()

        # 清空视频网格
        while self.video_grid.count():
            item = self.video_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        self.master_player = None
        self.position_slider.setEnabled(False)
        self.btn_play_pause.setEnabled(False)
        self.time_label.setText("00:00 / 00:00")

    # ---------- Load video ----------

    @Slot()
    def on_load_video_clicked(self) -> None:
        file_name = self.video_name_edit.text().strip()
        if not file_name:
            QMessageBox.warning(self, "提示", "请输入要加载的视频文件名，例如 video_064.mp4。")
            return

        if not self.folder_players:
            QMessageBox.warning(self, "提示", "请先添加至少一个文件夹。")
            return

        any_loaded = False
        for player in self.folder_players:
            ok = player.set_video_by_name(file_name)
            any_loaded = any_loaded or ok

        if not any_loaded:
            QMessageBox.warning(self, "提示", f"所有文件夹中都找不到 {file_name}。")
            return

        # 选第一个成功加载的作为 master
        for player in self.folder_players:
            if not player.player.source().isEmpty():
                self.set_master_player(player.player)
                break

        self.btn_play_pause.setEnabled(True)
        self.position_slider.setEnabled(True)
        self.btn_play_pause.setText("播放")

    def set_master_player(self, media_player: QMediaPlayer) -> None:
        if self.master_player is not None:
            try:
                self.master_player.positionChanged.disconnect(self.on_master_position_changed)
                self.master_player.durationChanged.disconnect(self.on_master_duration_changed)
            except TypeError:
                # 可能已经断开
                pass

        self.master_player = media_player
        self.master_player.positionChanged.connect(self.on_master_position_changed)
        self.master_player.durationChanged.connect(self.on_master_duration_changed)

        self.on_master_duration_changed(self.master_player.duration())

    # ---------- Playback controls ----------

    @Slot()
    def on_play_pause_clicked(self) -> None:
        if self.master_player is None:
            return

        if self.master_player.playbackState() == QMediaPlayer.PlayingState:
            for fp in self.folder_players:
                fp.pause()
            self.btn_play_pause.setText("播放")
        else:
            # 开始播放前先把从属播放器的位置对齐到 master 当前进度
            master_pos = self.master_player.position()
            for fp in self.folder_players:
                if fp.player is self.master_player:
                    continue
                fp.set_position(master_pos)
                fp.play()
            self.master_player.play()
            self.btn_play_pause.setText("暂停")

    @Slot()
    def on_slider_pressed(self) -> None:
        self._is_slider_dragging = True

    @Slot()
    def on_slider_released(self) -> None:
        self._is_slider_dragging = False
        self.apply_slider_position_to_players()

    @Slot(int)
    def on_slider_moved(self, value: int) -> None:
        # 拖动时只更新时间显示，真正跳转在释放时进行
        if self.master_player is None:
            return
        duration = self.master_player.duration() or 1
        position = int(duration * (value / 1000.0))
        self.update_time_label(position, duration)

    def apply_slider_position_to_players(self) -> None:
        if self.master_player is None:
            return
        value = self.position_slider.value()
        duration = self.master_player.duration() or 1
        position = int(duration * (value / 1000.0))

        for fp in self.folder_players:
            # 根据各自时长等比例跳转
            d = fp.duration() or duration
            mapped = int(d * (value / 1000.0))
            fp.set_position(mapped)

        self.update_time_label(position, duration)

    @Slot(int)
    def on_master_position_changed(self, position: int) -> None:
        if self.master_player is None or self._is_slider_dragging:
            return

        duration = self.master_player.duration() or 1
        slider_value = int(1000.0 * position / duration)
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(slider_value)
        self.position_slider.blockSignals(False)

        self.update_time_label(position, duration)

    @Slot(int)
    def on_master_duration_changed(self, duration: int) -> None:
        if duration <= 0:
            self.time_label.setText("00:00 / 00:00")
        else:
            self.update_time_label(self.master_player.position() if self.master_player else 0, duration)

    # ---------- Screenshot ----------

    @Slot()
    def on_select_screenshot_dir_clicked(self) -> None:
        path_str = QFileDialog.getExistingDirectory(self, "选择截图保存文件夹")
        if path_str:
            self.screenshot_dir_edit.setText(path_str)

    @Slot()
    def on_capture_screenshot_clicked(self) -> None:
        if not self.folder_players:
            QMessageBox.warning(self, "提示", "当前没有已加载的视频，无法截图。")
            return

        dir_text = self.screenshot_dir_edit.text().strip()
        if not dir_text:
            QMessageBox.warning(self, "提示", "请先选择保存截图的文件夹。")
            return

        out_dir = Path(dir_text)
        if not out_dir.exists():
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建保存目录：{e}")
                return

        base_name_input = self.screenshot_name_edit.text().strip()

        saved_count = 0
        for fp in self.folder_players:
            if fp.player.source().isEmpty():
                continue

            # 直接使用最近一帧图像，避免抓窗口导致的空白或变形
            if fp.last_frame is None or fp.last_frame.isNull():
                continue

            pixmap = QPixmap.fromImage(fp.last_frame)
            if pixmap.isNull():
                continue

            # 文件名：原视频文件夹名 + 用户填写的名字
            if base_name_input:
                base = f"{fp.folder.name}_{base_name_input}"
            else:
                base = fp.folder.name

            candidate = out_dir / f"{base}.png"
            idx = 1
            # 若文件已存在，则自动加序号避免覆盖
            while candidate.exists():
                candidate = out_dir / f"{base}_{idx}.png"
                idx += 1

            if pixmap.save(str(candidate), "PNG"):
                saved_count += 1

        if saved_count == 0:
            QMessageBox.information(self, "提示", "没有成功保存任何截图，可能当前画面为空或视频未加载。")
        else:
            QMessageBox.information(self, "提示", f"已保存 {saved_count} 张截图到:\n{out_dir}")

    # ---------- Helpers ----------

    def update_time_label(self, position_ms: int, duration_ms: int) -> None:
        def fmt(ms: int) -> str:
            s = ms // 1000
            m, s = divmod(s, 60)
            return f"{m:02d}:{s:02d}"

        self.time_label.setText(f"{fmt(position_ms)} / {fmt(duration_ms)}")


def main() -> None:
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
