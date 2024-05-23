import os
import random
import pandas as pd
import numpy as np
import logging
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, f1_score, precision_score, recall_score
from joblib import dump, load
import argparse
from sklearn.metrics import precision_recall_curve
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

argparser = argparse.ArgumentParser()
argparser.add_argument("--runpercent", type=int, default=50, help="Percentage of files to process.")
argparser.add_argument("--clear", action='store_true', help="Flag to clear the model and data directories.")
argparser.add_argument("--predict", action='store_true', help="Flag to predict new data.")
argparser.add_argument("--reuse", action='store_true', help="Flag to reuse existing training data if available.")
args = argparser.parse_args()

config = {
    "input_directory": "Data/IndicatorData",
    "model_output_directory": "Data/ModelData",
    "data_output_directory": "Data/ModelData/TrainingData",
    "prediction_output_directory": "Data/RFpredictions",
    "feature_importance_output": "Data/ModelData/FeatureImportances/feature_importance.parquet",
    "file_selection_percentage": args.runpercent,
    "target_column": "percent_change_Close",

    # Random Forest Classifier Parameters
    "n_estimators": 128,
    "criterion": "entropy",
    "max_depth": 15,
    "min_samples_split": 8,
    "min_samples_leaf": 4,
    "min_weight_fraction_leaf": 0,
    "max_features": 0.10,
    "max_leaf_nodes": None,
    "min_impurity_decrease": 0,
    "bootstrap": True,
    "oob_score": False,
    "random_state": 3301,
    "verbose": 2,
    "warm_start": False,
    "class_weight": {0: 1.0, 1: 1.75},
    "ccp_alpha": 0,
    "max_samples": None
}





def prepare_training_data(input_directory, output_directory, file_selection_percentage, target_column, reuse, date_column):
    output_file = os.path.join(output_directory, 'training_data.parquet')
    if reuse and os.path.exists(output_file):
        logging.info("Reusing existing training data.")
        print("Reusing existing training data.")
        return pd.read_parquet(output_file)
    
    logging.info("Preparing new training data.")
    all_files = [f for f in os.listdir(input_directory) if f.endswith('.parquet')]
    selected_files = random.sample(all_files, int(len(all_files) * file_selection_percentage / 100))
    
    if os.path.exists(output_file):
        os.remove(output_file) 
    pbar = tqdm(total=len(selected_files), desc="Processing files")
    
    all_data = []
    
    for file in selected_files:
        df = pd.read_parquet(os.path.join(input_directory, file))
        # Ensure the df is not empty and has at least the target and 50 rows 
        if df.shape[0] > 50 and target_column in df.columns and date_column in df.columns:
            # Ensure the date column is in datetime format
            df[date_column] = pd.to_datetime(df[date_column])
            # Shift the target column by 1
            df[target_column] = df[target_column].shift(-1)
            # Remove the first 2 rows and the last 2 rows
            df = df.iloc[2:-2]
            # Drop rows with NaN in the target column
            df = df.dropna(subset=[target_column])
            # Filter target column values
            df = df[(df[target_column] <= 10000) & (df[target_column] >= -10000)]
            all_data.append(df)
        pbar.update(1)
    pbar.close()
    # Concatenate the dataframes together
    combined_df = pd.concat(all_data)
    # Group by date, shuffle within each group, and then concatenate
    grouped = combined_df.groupby(date_column)
    shuffled_groups = [group.sample(frac=1).reset_index(drop=True) for _, group in grouped]
    final_df = pd.concat(shuffled_groups).reset_index(drop=True)
    # Save the dataframe to a parquet file
    final_df.to_parquet(output_file, index=False)
    return final_df






def train_random_forest(training_data, config, confidence_threshold_pos=0.62, confidence_threshold_neg=0.62):
    logging.info("Training Random Forest model.")
    
    #remove the old model file
    model_output_path = os.path.join(config['model_output_directory'], 'random_forest_model.joblib')
    if os.path.exists(model_output_path):
        os.remove(model_output_path)

    # Separate features and target
    X = training_data.drop(columns=[config['target_column']])
    y = training_data[config['target_column']]
    
    # Binarize the target column
    y = y.apply(lambda x: 1 if x > 0 else 0)
    
    # Remove datetime columns from X
    datetime_columns = X.select_dtypes(include=['datetime64']).columns
    X = X.drop(columns=datetime_columns)
    
    # Split the data into train and validation sets
    split_index = int(len(X) * 0.9)
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
    
    # Train the Random Forest classifier
    clf = RandomForestClassifier(
        n_estimators=config['n_estimators'],
        criterion=config['criterion'],
        max_depth=config['max_depth'],
        min_samples_split=config['min_samples_split'],
        min_samples_leaf=config['min_samples_leaf'],
        min_weight_fraction_leaf=config['min_weight_fraction_leaf'],
        max_features=config['max_features'],
        max_leaf_nodes=config['max_leaf_nodes'],
        min_impurity_decrease=config['min_impurity_decrease'],
        bootstrap=config['bootstrap'],
        oob_score=config['oob_score'],
        random_state=config['random_state'],
        verbose=config['verbose'],
        warm_start=config['warm_start'],
        class_weight={0: 1, 1: 1.5},  # Increase class weight for class 1
        ccp_alpha=config['ccp_alpha'],
        max_samples=config['max_samples'],
        n_jobs=-1  # Use all available processors
    )
    
    clf.fit(X_train, y_train)
    
    # Make predictions with confidence thresholds for both classes
    y_pred_proba = clf.predict_proba(X_test)
    y_pred = np.where(
        y_pred_proba[:, 1] >= confidence_threshold_pos, 1,
        np.where(y_pred_proba[:, 0] >= confidence_threshold_neg, 0, -1)
    )
    
    # Filter out undecided predictions
    mask = y_pred != -1
    y_test_filtered = y_test[mask]
    y_pred_filtered = y_pred[mask]
    
    # Evaluate the model
    accuracy = accuracy_score(y_test_filtered, y_pred_filtered)
    f1 = f1_score(y_test_filtered, y_pred_filtered, average='weighted')
    precision = precision_score(y_test_filtered, y_pred_filtered, average='weighted')
    recall = recall_score(y_test_filtered, y_pred_filtered, average='weighted')
    
    logging.info(f"Accuracy: {accuracy}")
    logging.info(f"F1 Score: {f1}")
    logging.info(f"Precision: {precision}")
    logging.info(f"Recall: {recall}")
    
    print(classification_report(y_test_filtered, y_pred_filtered, zero_division=0))
    
    # Save the model
    model_output_path = os.path.join(config['model_output_directory'], 'random_forest_model.joblib')
    dump(clf, model_output_path)
    
    logging.info(f"Model saved to {model_output_path}")
    
    # Save feature importances
    feature_importances = pd.DataFrame({
        'feature': X_train.columns,
        'importance': clf.feature_importances_
    }).sort_values(by='importance', ascending=False)
    
    feature_importances['importance'] = feature_importances['importance'].round(5)
    
    feature_importance_output_path = os.path.join(config['feature_importance_output'])
    feature_importances.to_parquet(feature_importance_output_path, index=False)
    
    logging.info(f"Feature importances saved to {feature_importance_output_path}")

def predict_and_save(input_directory, model_path, output_directory, target_column, date_column):
    logging.info("Loading the trained model.")
    
    # Remove all the parquet files in the output directory
    for file in os.listdir(output_directory):
        if file.endswith('.parquet'):
            os.remove(os.path.join(output_directory, file))
    
    # Load the trained model
    clf = load(model_path)
    
    # Get the list of files to process
    all_files = [f for f in os.listdir(input_directory) if f.endswith('.parquet')]
    
    # Initialize progress bar
    pbar = tqdm(total=len(all_files), desc="Processing files")
    
    for file in all_files:
        df = pd.read_parquet(os.path.join(input_directory, file))
        # Ensure the date column is in datetime format
        df[date_column] = pd.to_datetime(df[date_column])
        
        # Remove datetime and target columns from X
        datetime_columns = df.select_dtypes(include=['datetime64']).columns
        X = df.drop(columns=[date_column, target_column] + list(datetime_columns))
        
        # Make predictions with probabilities
        y_pred_proba = clf.predict_proba(X)
        
        # Append predictions to dataframe
        df['UpProbability'] = y_pred_proba[:, 1]
        df['UpPrediction'] = (df['UpProbability'] >= 0.62).astype(int)
        
        # Save the dataframe with predictions
        output_file_path = os.path.join(output_directory, file)
        df.to_parquet(output_file_path, index=False)
        
        pbar.update(1)
    
    pbar.close()
    logging.info(f"Predictions saved to {output_directory}")




def main():
    if not args.predict:
        training_data = prepare_training_data(
            input_directory=config['input_directory'],
            output_directory=config['data_output_directory'],
            file_selection_percentage=config['file_selection_percentage'],
            target_column=config['target_column'],
            reuse=args.reuse,
            date_column='Date'  # Assuming 'Date' is the name of your date column
        )
        logging.info("Data preparation complete.")
        
        train_random_forest(training_data, config)
    else:
        predict_and_save(
            input_directory=config['input_directory'],
            model_path=os.path.join(config['model_output_directory'], 'random_forest_model.joblib'),
            output_directory=config['prediction_output_directory'],
            target_column=config['target_column'],
            date_column='Date'
        )

if __name__ == "__main__":
    main()
