from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow, QTabWidget

from oracle41_open.gui.views.activity_view import ActivityView
from oracle41_open.gui.views.notes_saved_views_view import NotesSavedViewsView
from oracle41_open.gui.views.overview_view import OverviewView
from oracle41_open.gui.views.portfolio_view import PortfolioView
from oracle41_open.gui.views.settings_view import SettingsView
from oracle41_open.gui.views.snapshots_view import SnapshotsView
from oracle41_open.gui.views.token_detail_view import TokenDetailView
from oracle41_open.gui.views.watchlist_view import WatchlistView

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer
    from oracle41_open.core.models import Chain


class MainWindow(QMainWindow):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self.setWindowTitle("Oracle41 Open")
        self.setWindowIcon(QIcon.fromTheme("io.github.damianpitt.oracle41_open"))
        self.resize(1180, 760)

        tabs = QTabWidget(self)
        overview_view = OverviewView(container=container)
        activity_view = ActivityView(container=container)
        token_detail_view = TokenDetailView(container=container)
        tabs.addTab(overview_view, "Overview")
        tabs.addTab(activity_view, "Activity")
        tabs.addTab(token_detail_view, "Token Detail")
        watchlist_view = WatchlistView(
            container=container,
            open_wallet_in_overview=lambda address, chain: self._open_wallet_in_overview(
                tabs=tabs,
                overview_view=overview_view,
                address=address,
                chain=chain,
            ),
        )
        tabs.addTab(
            watchlist_view,
            "Watchlist",
        )
        tabs.addTab(PortfolioView(container=container), "Portfolio")
        tabs.addTab(
            NotesSavedViewsView(
                container=container,
                open_activity_with_filters=lambda chain, filters: self._open_activity_with_filters(
                    tabs=tabs,
                    activity_view=activity_view,
                    chain=chain,
                    filters=filters,
                ),
                open_token_detail_with_filters=lambda chain, filters: self._open_token_detail_with_filters(
                    tabs=tabs,
                    token_detail_view=token_detail_view,
                    chain=chain,
                    filters=filters,
                ),
            ),
            "Notes & Views",
        )
        tabs.addTab(
            SnapshotsView(
                container=container,
                open_wallet_in_overview=lambda address, chain: self._open_wallet_in_overview(
                    tabs=tabs,
                    overview_view=overview_view,
                    address=address,
                    chain=chain,
                ),
            ),
            "Snapshots",
        )
        tabs.addTab(SettingsView(container=container), "Settings")
        self.setCentralWidget(tabs)

    def _open_wallet_in_overview(
        self,
        tabs: QTabWidget,
        overview_view: OverviewView,
        address: str,
        chain: Chain,
    ) -> None:
        overview_index = tabs.indexOf(overview_view)
        if overview_index >= 0:
            tabs.setCurrentIndex(overview_index)
        overview_view.load_wallet(address=address, chain=chain)

    def _open_activity_with_filters(
        self,
        tabs: QTabWidget,
        activity_view: ActivityView,
        chain: Chain,
        filters: dict[str, object],
    ) -> None:
        activity_index = tabs.indexOf(activity_view)
        if activity_index >= 0:
            tabs.setCurrentIndex(activity_index)
        activity_view.apply_quick_filters(chain=chain, filters=filters)

    def _open_token_detail_with_filters(
        self,
        tabs: QTabWidget,
        token_detail_view: TokenDetailView,
        chain: Chain,
        filters: dict[str, object],
    ) -> None:
        token_detail_index = tabs.indexOf(token_detail_view)
        if token_detail_index >= 0:
            tabs.setCurrentIndex(token_detail_index)
        token_detail_view.apply_quick_filters(chain=chain, filters=filters)
