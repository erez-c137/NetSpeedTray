"""
Specialized exporter utilities for NetSpeedTray data and visualizations.
"""
import os
import csv
import logging
from datetime import datetime
from typing import List, Tuple, Optional, Any

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QWidget

from netspeedtray import constants

logger = logging.getLogger(__name__)

class DataExporter:
    """
    Handles persisting application data to external files (CSV, PNG, etc).
    """

    @staticmethod
    def export_to_csv(parent: QWidget, i18n: Any, history_data: List[Tuple[datetime, float, float]]) -> Optional[str]:
        """
        Prompts user for a location and exports history tuples to a CSV file.
        Returns the file path if successful, else None.
        """
        if not history_data:
            QMessageBox.warning(parent, i18n.WARNING_TITLE, i18n.NO_HISTORY_DATA_MESSAGE)
            return None

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = constants.export.CSV_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            default_path = os.path.join(constants.export.DEFAULT_EXPORT_PATH, suggested_name)
            
            file_path, _ = QFileDialog.getSaveFileName(
                parent, i18n.EXPORT_CSV_TITLE, default_path, i18n.CSV_FILE_FILTER
            )
            
            if not file_path:
                return None

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, "w", newline="", encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    i18n.CSV_HEADER_TIMESTAMP, 
                    i18n.CSV_HEADER_UPLOAD_MBPS, 
                    i18n.CSV_HEADER_DOWNLOAD_MBPS
                ])
                
                mega = 1_000_000
                for ts, up_bytes, down_bytes in history_data:
                    writer.writerow([
                        ts.isoformat(),
                        f"{(up_bytes * 8 / mega):.4f}",
                        f"{(down_bytes * 8 / mega):.4f}"
                    ])

            QMessageBox.information(
                parent, i18n.SUCCESS_TITLE, 
                i18n.EXPORT_SUCCESS_MESSAGE.format(file_path=file_path)
            )
            return file_path

        except PermissionError:
            logger.error(f"Permission denied exporting to {file_path}", exc_info=True)
            QMessageBox.critical(parent, i18n.ERROR_TITLE, i18n.PERMISSION_DENIED_MESSAGE)
        except Exception as e:
            logger.error(f"Error in export_to_csv: {e}", exc_info=True)
            QMessageBox.critical(parent, i18n.ERROR_TITLE, i18n.EXPORT_ERROR_MESSAGE.format(error=str(e)))
        
        return None

    @staticmethod
    def save_graph_image(parent: QWidget, i18n: Any, figure: Any) -> Optional[str]:
        """
        Prompts user for a location and saves a Matplotlib figure as a PNG.
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = constants.export.IMAGE_SUGGESTED_NAME_TEMPLATE.format(timestamp=timestamp)
            default_path = os.path.join(constants.export.DEFAULT_EXPORT_PATH, suggested_name)
            
            file_path, _ = QFileDialog.getSaveFileName(
                parent, i18n.EXPORT_GRAPH_IMAGE_TITLE, default_path, i18n.PNG_FILE_FILTER
            )
            
            if not file_path:
                return None

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Use figure's current facecolor for saved image background
            figure.savefig(
                file_path, 
                bbox_inches='tight', 
                dpi=constants.export.IMAGE_DPI, 
                facecolor=figure.get_facecolor()
            )
            
            QMessageBox.information(
                parent, i18n.SUCCESS_TITLE, 
                i18n.EXPORT_SUCCESS_MESSAGE.format(file_path=file_path)
            )
            return file_path

        except PermissionError:
            logger.error(f"Permission denied saving image to {file_path}", exc_info=True)
            QMessageBox.critical(parent, i18n.ERROR_TITLE, i18n.PERMISSION_DENIED_MESSAGE)
        except Exception as e:
            logger.error(f"Error in save_graph_image: {e}", exc_info=True)
            QMessageBox.critical(parent, i18n.ERROR_TITLE, i18n.EXPORT_ERROR_MESSAGE.format(error=str(e)))
            
        return None
