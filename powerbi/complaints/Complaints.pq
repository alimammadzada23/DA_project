// Create DataFolder as a text parameter pointing to the exported local folder.
let
    Source = Parquet.Document(File.Contents(DataFolder & "/Complaints.parquet")),
    Typed = Table.TransformColumnTypes(Source, {
        {"record_id", Int64.Type}, {"complaint_id", type text},
        {"record_date", type date}, {"date_sent", type date},
        {"company", type text}, {"product", type text}, {"sub_product", type text},
        {"issue", type text}, {"sub_issue", type text}, {"state", type text},
        {"response", type text}, {"timely", type text}, {"submitted_via", type text}
    })
in
    Typed
