from pyspark.sql import SparkSession
storageAccount = "undertonestorage"
spark = SparkSession.builder.appName("DataFrame").getOrCreate()
spark.conf.set(f"fs.azure.account.key.{storageAccount}.blob.core.windows.net", "AFzAzkDZ3kqaGPKZP8DhXcCeOjrWt5WfefQ3/BnG05mKEdciuMZ1B9/wVfoiAQM0+VZRxXVVJ6zI+AStwcEpKg==")

import dlt
import pyspark.sql.types as T
from pyspark.sql.functions import *

# Event Hubs configuration
EH_NAMESPACE                    = spark.conf.get("iot.ingestion.eh.namespace")
EH_NAME                         = spark.conf.get("iot.ingestion.eh.name")

EH_CONN_SHARED_ACCESS_KEY_NAME  = spark.conf.get("iot.ingestion.eh.accessKeyName")
EH_CONN_SHARED_ACCESS_KEY_VALUE = spark.conf.get("iot.ingestion.eh.accessKeyValue")

EH_CONN_STR                     = f"Endpoint=sb://{EH_NAMESPACE}.servicebus.windows.net/;SharedAccessKeyName={EH_CONN_SHARED_ACCESS_KEY_NAME};SharedAccessKey={EH_CONN_SHARED_ACCESS_KEY_VALUE}"
# Kafka Consumer configuration

KAFKA_OPTIONS = {
  "kafka.bootstrap.servers"  : f"{EH_NAMESPACE}.servicebus.windows.net:9093",
  "subscribe"                : EH_NAME,
  "kafka.sasl.mechanism"     : "PLAIN",
  "kafka.security.protocol"  : "SASL_SSL",
  "kafka.sasl.jaas.config"   : f"kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule required username=\"$ConnectionString\" password=\"{EH_CONN_STR}\";",
  "kafka.request.timeout.ms" : spark.conf.get("iot.ingestion.kafka.requestTimeout"),
  "kafka.session.timeout.ms" : spark.conf.get("iot.ingestion.kafka.sessionTimeout"),
  "maxOffsetsPerTrigger"     : spark.conf.get("iot.ingestion.spark.maxOffsetsPerTrigger"),
  "failOnDataLoss"           : spark.conf.get("iot.ingestion.spark.failOnDataLoss"),
  "startingOffsets"          : spark.conf.get("iot.ingestion.spark.startingOffsets")
}

# PAYLOAD SCHEMA
payload_ddl = """battery_level BIGINT, c02_level BIGINT, cca2 STRING, cca3 STRING, cn STRING, device_id BIGINT, device_name STRING, humidity BIGINT, ip STRING, latitude DOUBLE, lcd STRING, longitude DOUBLE, scale STRING, temp  BIGINT, timestamp BIGINT"""
payload_schema = T._parse_datatype_string(payload_ddl)

# Basic record parsing and adding ETL audit columns
def parse(df):
  return (df
    .withColumn("records", col("value").cast("string"))
    .withColumn("parsed_records", from_json(col("records"), payload_schema))
    .withColumn("iot_event_timestamp", expr("cast(from_unixtime(parsed_records.timestamp / 1000) as timestamp)"))
    .withColumn("eh_enqueued_timestamp", expr("timestamp"))
    .withColumn("eh_enqueued_date", expr("to_date(timestamp)"))
    .withColumn("etl_processed_timestamp", col("current_timestamp"))
    .withColumn("etl_rec_uuid", expr("uuid()"))
    .drop("records", "value", "key")
  )

@dlt.create_table(
  comment="Raw IOT Events",
  table_properties={
    "quality": "bronze",
    "pipelines.reset.allowed": "false" # preserves the data in the delta table if you do full refresh
  },
  partition_cols=["eh_enqueued_date"],
  path="wasbs://sink@undertonestorage.blob.core.windows.net/delta/bronze/iot_blob"
)
@dlt.expect("valid_topic", "topic IS NOT NULL")
@dlt.expect("valid records", "parsed_records IS NOT NULL")
def iot_raw():
  return (
   spark.readStream
    .format("kafka")
    .options(**KAFKA_OPTIONS)
    .load()
    .transform(parse)
  )