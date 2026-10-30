"""刮削编排子包。

各协作者按用途分文件：service（门面/主链路）、config_resolver、metadata_resolver、
media_pipeline、output_writer、p115_storage_provider、ports（协作者依赖的契约）。
预演走 ScraperService.preview()，没有独立的预览服务。
"""
