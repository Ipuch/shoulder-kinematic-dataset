# test
from dash import Dash, dcc, html, Input, Output, State, callback
import plotly.express as px
import pandas as pd
import io
import base64
from spartacus import load


# TODO : Put the correct joint in the article.
# TODO : Add a curve directly from the app running
# TODO : Update graph when data is added.
# TODO : check for name (flexion extension abduction adduction etc.. with positive and negative value + adapted name if needed for specific joint)
# Question à aborder sur la forme des données.
extracted_data = load().import_confident_data()
extracted_data.angle_translation = "angle"
# TODO : do a function to change the name of the degree of freedom
# Todo : Change the name of the function to be more clean ==> not draft anymore.
# Begin to check curves for outlier.
for i in range(extracted_data.degree_of_freedom.size):
    if extracted_data.degree_of_freedom[i] == "1":
        extracted_data.degree_of_freedom[i] = "flexion"
    elif extracted_data.degree_of_freedom[i] == "2":
        extracted_data.degree_of_freedom[i] = "abduction"
    elif extracted_data.degree_of_freedom[i] == "3":
        extracted_data.degree_of_freedom[i] = "external_rotation"
app = Dash(__name__)

app.layout = html.Div(
    [  # Global Title of the graph
        html.H4("Kinematics of the shoulder joint"),
        html.Button("Download CSV", id="btn_csv"),
        dcc.Download(id="download-dataframe-csv"),
        # Plot the graph
        dcc.Graph(id="graph"),
        # Show the different options in different collumn
        dcc.Dropdown(
            id="humeral_motion",
            options=sorted([i for i in extracted_data.humeral_motion.unique()]),
            value=sorted([i for i in extracted_data.humeral_motion.unique()])[0],
        ),
        dcc.Checklist(
            id="joint",
            options=sorted([i for i in extracted_data.joint.unique()]),
            value=sorted([i for i in extracted_data.joint.unique()]),
            inline=True,
        ),
        dcc.Dropdown(
            options=sorted([i for i in extracted_data.angle_translation.unique()]),
            value=sorted([i for i in extracted_data.angle_translation.unique()])[0],
            id="angle_translation",
        ),
        dcc.Upload(
            id="upload-data",
            children=html.Div(["Drag and Drop or ", html.A("Select Files")]),
            style={
                "width": "100%",
                "height": "60px",
                "lineHeight": "60px",
                "borderWidth": "1px",
                "borderStyle": "dashed",
                "borderRadius": "5px",
                "textAlign": "center",
                "margin": "10px",
            },
            # Allow multiple files to be uploaded
            multiple=True,
        ),
        html.Div(id="output"),
    ]
)


# Import data
@callback(
    Output("output", "children"),
    Input("upload-data", "contents"),
)
def update_output(contents):
    global extracted_data

    if contents is not None:
        content_type, content_string = contents[0].split(",")

        decoded = base64.b64decode(content_string)
        df = pd.read_csv(io.StringIO(decoded.decode("utf-8")))

        frames = [extracted_data, df]

        extracted_data = pd.concat(frames)
        print(extracted_data.size)
    return extracted_data.size


# Export data
# TODO : what should be exported when the user ask it (only what is visible or everything)
@callback(
    Output("download-dataframe-csv", "data"),
    State("humeral_motion", "value"),
    State("joint", "value"),
    State("angle_translation", "value"),
    Input("btn_csv", "n_clicks"),
    prevent_initial_call=True,
)
def export_data(humeral_motion, joint, angle_translation, n_clicks):
    df = extracted_data  # replace with your own data source
    mask_joint = df.joint.isin(joint)
    mask_mvt = df.humeral_motion.isin([humeral_motion])
    # We have to put Angle translation in a list because it is a string
    mask_angle_translation = df.angle_translation.isin([angle_translation])
    # In order to have the data in the correct orger we have to define a list ordering the data
    list_joint_graph_base_in_order = ["humerothoracic", "glenohumeral", "scapulothoracic", "acromioclavicular"]

    data_to_export = df[mask_mvt & mask_joint & mask_angle_translation]
    return dcc.send_data_frame(data_to_export.to_csv, "mydf.csv")


@app.callback(
    Output("graph", "figure"),
    Input("humeral_motion", "value"),
    Input("joint", "value"),
    Input("angle_translation", "value"),
)
def update_line_chart(humeral_motion, joint, angle_translation):
    df = extracted_data  # replace with your own data source
    mask_joint = df.joint.isin(joint)
    mask_mvt = df.humeral_motion.isin([humeral_motion])
    # We have to put Angle translation in a list because it is a string
    mask_angle_translation = df.angle_translation.isin([angle_translation])
    # In order to have the data in the correct orger we have to define a list ordering the data
    list_joint_graph_base_in_order = ["humerothoracic", "glenohumeral", "scapulothoracic", "acromioclavicular"]
    # Adapt the list to the number of degree of freedom selectionned by the user.
    list_to_plot_in_order = []
    for name_joint in list_joint_graph_base_in_order:
        if name_joint in joint:
            list_to_plot_in_order.append(name_joint)

    if angle_translation == "angle":
        list_orga = ["flexion", "abduction", "external_rotation"]
    elif angle_translation == "translation":
        list_orga = ["X", "Y", "Z"]
    fig = px.scatter(
        df[mask_mvt & mask_joint & mask_angle_translation],
        x="humerothoracic_angle",
        y="value",
        color="article",
        facet_row="joint",
        facet_col="degree_of_freedom",
        category_orders={"degree_of_freedom": list_orga, "joint": list_to_plot_in_order},
    )
    # Allow to remove the "Mvt=" in the legend
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    # here to switch between different layout

    fig.update_layout(
        # If we fix only the height the width will be adapted to the size of the screen
        # However not fixing the height AND the width make the graph not readable
        height=800,
        # width=1500,
        paper_bgcolor="rgba(255,255,255,1)",
        plot_bgcolor="rgba(255,255,255,1)",
        legend=dict(
            title_font_family="Times New Roman",
            font=dict(family="Times New Roman", color="black", size=16),
            orientation="v",
            x=1,
            y=1,
        ),
        font=dict(
            size=16,
            family="Times New Roman",
        ),
        yaxis=dict(color="black"),
        template="simple_white",
        boxgap=0.5,
    )
    return fig


if __name__ == "__main__":
    app.run_server(debug=True)
