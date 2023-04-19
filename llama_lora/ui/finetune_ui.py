import os
import json
import time
import traceback
import re
from datetime import datetime
import gradio as gr
import math
from random_word import RandomWords

from transformers import TrainerCallback

from ..globals import Global
from ..models import (
    get_new_base_model, get_tokenizer,
    clear_cache, unload_models)
from ..utils.data import (
    get_available_template_names,
    get_available_dataset_names,
    get_dataset_content,
    get_available_lora_model_names
)
from ..utils.prompter import Prompter


def random_hyphenated_word():
    r = RandomWords()
    word1 = r.get_random_word()
    word2 = r.get_random_word()
    return word1 + '-' + word2


def random_name():
    current_datetime = datetime.now()
    formatted_datetime = current_datetime.strftime("%Y-%m-%d-%H-%M-%S")
    return f"{random_hyphenated_word()}-{formatted_datetime}"


def reload_selections(current_template, current_dataset):
    available_template_names = get_available_template_names()
    available_template_names_with_none = available_template_names + ["None"]
    if current_template not in available_template_names_with_none:
        current_template = None
    current_template = current_template or next(
        iter(available_template_names_with_none), None)

    available_dataset_names = get_available_dataset_names()
    if current_dataset not in available_dataset_names:
        current_dataset = None
    current_dataset = current_dataset or next(
        iter(available_dataset_names), None)

    available_lora_models = ["-"] + get_available_lora_model_names()

    return (
        gr.Dropdown.update(
            choices=available_template_names_with_none,
            value=current_template),
        gr.Dropdown.update(
            choices=available_dataset_names,
            value=current_dataset),
        gr.Dropdown.update(choices=available_lora_models)
    )


def handle_switch_dataset_source(source):
    if source == "Text Input":
        return gr.Column.update(visible=True), gr.Column.update(visible=False)
    else:
        return gr.Column.update(visible=False), gr.Column.update(visible=True)


def handle_switch_dataset_text_format(format):
    if format == "Plain Text":
        return gr.Column.update(visible=True)
    return gr.Column.update(visible=False)


def load_sample_dataset_to_text_input(format):
    if format == "JSON":
        return gr.Code.update(value=sample_json_text_value)
    if format == "JSON Lines":
        return gr.Code.update(value=sample_jsonl_text_value)
    else:  # Plain Text
        return gr.Code.update(value=sample_plain_text_value)


def get_data_from_input(load_dataset_from, dataset_text, dataset_text_format,
                        dataset_plain_text_input_variables_separator,
                        dataset_plain_text_input_and_output_separator,
                        dataset_plain_text_data_separator,
                        dataset_from_data_dir, prompter):
    if load_dataset_from == "Text Input":
        if dataset_text_format == "JSON":
            data = json.loads(dataset_text)

        elif dataset_text_format == "JSON Lines":
            lines = dataset_text.split('\n')
            data = []
            for i, line in enumerate(lines):
                line_number = i + 1
                try:
                    data.append(json.loads(line))
                except Exception as e:
                    raise ValueError(
                        f"Error parsing JSON on line {line_number}: {e}")

        else:  # Plain Text
            data = parse_plain_text_input(
                dataset_text,
                (
                    dataset_plain_text_input_variables_separator or
                    default_dataset_plain_text_input_variables_separator
                ).replace("\\n", "\n"),
                (
                    dataset_plain_text_input_and_output_separator or
                    default_dataset_plain_text_input_and_output_separator
                ).replace("\\n", "\n"),
                (
                    dataset_plain_text_data_separator or
                    default_dataset_plain_text_data_separator
                ).replace("\\n", "\n"),
                prompter.get_variable_names()
            )

    else:  # Load dataset from data directory
        data = get_dataset_content(dataset_from_data_dir)

    return data


def refresh_preview(
    template,
    load_dataset_from,
    dataset_from_data_dir,
    dataset_text,
    dataset_text_format,
    dataset_plain_text_input_variables_separator,
    dataset_plain_text_input_and_output_separator,
    dataset_plain_text_data_separator,
    max_preview_count,
):
    try:
        prompter = Prompter(template)
        variable_names = prompter.get_variable_names()

        data = get_data_from_input(
            load_dataset_from=load_dataset_from,
            dataset_text=dataset_text,
            dataset_text_format=dataset_text_format,
            dataset_plain_text_input_variables_separator=dataset_plain_text_input_variables_separator,
            dataset_plain_text_input_and_output_separator=dataset_plain_text_input_and_output_separator,
            dataset_plain_text_data_separator=dataset_plain_text_data_separator,
            dataset_from_data_dir=dataset_from_data_dir,
            prompter=prompter
        )

        train_data = prompter.get_train_data_from_dataset(
            data, max_preview_count)

        train_data = train_data[:max_preview_count]

        data_count = len(data)

        headers = ['Prompt', 'Completion']
        preview_data = [
            [item.get("prompt", ""), item.get("completion", "")]
            for item in train_data
        ]

        if not prompter.template_module:
            variable_names = prompter.get_variable_names()
            headers += [f"Variable: {variable_name}" for variable_name in variable_names]
            variables = [
                [item.get(f"_var_{name}", "") for name in variable_names]
                for item in train_data
            ]
            preview_data = [d + v for d, v in zip(preview_data, variables)]

        preview_info_message = f"The dataset has about {data_count} item(s)."
        if data_count > max_preview_count:
            preview_info_message += f" Previewing the first {max_preview_count}."

        info_message = f"about {data_count} item(s)."
        if load_dataset_from == "Data Dir":
            info_message = "This dataset contains about " + info_message
        update_message = gr.Markdown.update(info_message, visible=True)

        return gr.Dataframe.update(value={'data': preview_data, 'headers': headers}), gr.Markdown.update(preview_info_message), update_message, update_message
    except Exception as e:
        update_message = gr.Markdown.update(
            f"<span class=\"finetune_dataset_error_message\">Error: {e}.</span>", visible=True)
        return gr.Dataframe.update(value={'data': [], 'headers': []}), gr.Markdown.update("Set the dataset in the \"Prepare\" tab, then preview it here."), update_message, update_message


def refresh_dataset_items_count(
    template,
    load_dataset_from,
    dataset_from_data_dir,
    dataset_text,
    dataset_text_format,
    dataset_plain_text_input_variables_separator,
    dataset_plain_text_input_and_output_separator,
    dataset_plain_text_data_separator,
    max_preview_count,
):
    try:
        prompter = Prompter(template)
        variable_names = prompter.get_variable_names()

        data = get_data_from_input(
            load_dataset_from=load_dataset_from,
            dataset_text=dataset_text,
            dataset_text_format=dataset_text_format,
            dataset_plain_text_input_variables_separator=dataset_plain_text_input_variables_separator,
            dataset_plain_text_input_and_output_separator=dataset_plain_text_input_and_output_separator,
            dataset_plain_text_data_separator=dataset_plain_text_data_separator,
            dataset_from_data_dir=dataset_from_data_dir,
            prompter=prompter
        )

        train_data = prompter.get_train_data_from_dataset(
            data)
        data_count = len(train_data)

        preview_info_message = f"The dataset contains {data_count} item(s)."
        if data_count > max_preview_count:
            preview_info_message += f" Previewing the first {max_preview_count}."

        info_message = f"{data_count} item(s)."
        if load_dataset_from == "Data Dir":
            info_message = "This dataset contains " + info_message
        update_message = gr.Markdown.update(info_message, visible=True)

        return gr.Markdown.update(preview_info_message), update_message, update_message, gr.Slider.update(maximum=math.floor(data_count / 2))
    except Exception as e:
        update_message = gr.Markdown.update(
            f"<span class=\"finetune_dataset_error_message\">Error: {e}.</span>", visible=True)

        trace = traceback.format_exc()
        traces = [s.strip() for s in re.split("\n * File ", trace)]
        templates_path = os.path.join(Global.data_dir, "templates")
        traces_to_show = [s for s in traces if os.path.join(
            Global.data_dir, "templates") in s]
        traces_to_show = [re.sub(" *\n *", ": ", s) for s in traces_to_show]
        if len(traces_to_show) > 0:
            update_message = gr.Markdown.update(
                f"<span class=\"finetune_dataset_error_message\">Error: {e} ({','.join(traces_to_show)}).</span>", visible=True)

        return gr.Markdown.update("Set the dataset in the \"Prepare\" tab, then preview it here."), update_message, update_message, gr.Slider.update(maximum=1)


def parse_plain_text_input(
    value,
    variables_separator, input_output_separator, data_separator,
    variable_names
):
    items = value.split(data_separator)
    result = []
    for item in items:
        parts = item.split(input_output_separator)
        variables = get_val_from_arr(parts, 0, "").split(variables_separator)
        variables = [it.strip() for it in variables]
        variables_dict = {name: var for name,
                          var in zip(variable_names, variables)}
        output = get_val_from_arr(parts, 1, "").strip()
        result.append({'variables': variables_dict, 'output': output})
    return result


should_training_progress_track_tqdm = True

if Global.gpu_total_cores is not None and Global.gpu_total_cores > 2560:
    should_training_progress_track_tqdm = False


def do_train(
    # Dataset
    template,
    load_dataset_from,
    dataset_from_data_dir,
    dataset_text,
    dataset_text_format,
    dataset_plain_text_input_variables_separator,
    dataset_plain_text_input_and_output_separator,
    dataset_plain_text_data_separator,
    # Training Options
    max_seq_length,
    evaluate_data_count,
    micro_batch_size,
    gradient_accumulation_steps,
    epochs,
    learning_rate,
    train_on_inputs,
    lora_r,
    lora_alpha,
    lora_dropout,
    lora_target_modules,
    save_steps,
    save_total_limit,
    logging_steps,
    model_name,
    continue_from_model,
    continue_from_checkpoint,
    progress=gr.Progress(track_tqdm=should_training_progress_track_tqdm),
):
    try:
        base_model_name = Global.base_model_name

        resume_from_checkpoint = None
        if continue_from_model == "-" or continue_from_model == "None":
            continue_from_model = None
        if continue_from_checkpoint == "-" or continue_from_checkpoint == "None":
            continue_from_checkpoint = None
        if continue_from_model:
            resume_from_checkpoint = os.path.join(Global.data_dir, "lora_models", continue_from_model)
            if continue_from_checkpoint:
                resume_from_checkpoint = os.path.join(resume_from_checkpoint, continue_from_checkpoint)
                will_be_resume_from_checkpoint_file = os.path.join(resume_from_checkpoint, "pytorch_model.bin")
                if not os.path.exists(will_be_resume_from_checkpoint_file):
                    raise ValueError(f"Unable to resume from checkpoint {continue_from_model}/{continue_from_checkpoint}. Resuming is only possible from checkpoints stored locally in the data directory. Please ensure that the file '{will_be_resume_from_checkpoint_file}' exists.")
            else:
                will_be_resume_from_checkpoint_file = os.path.join(resume_from_checkpoint, "adapter_model.bin")
                if not os.path.exists(will_be_resume_from_checkpoint_file):
                    raise ValueError(f"Unable to continue from model {continue_from_model}. Continuation is only possible from models stored locally in the data directory. Please ensure that the file '{will_be_resume_from_checkpoint_file}' exists.")

        output_dir = os.path.join(Global.data_dir, "lora_models", model_name)
        if os.path.exists(output_dir):
            if (not os.path.isdir(output_dir)) or os.path.exists(os.path.join(output_dir, 'adapter_config.json')):
                raise ValueError(
                    f"The output directory already exists and is not empty. ({output_dir})")

        if not should_training_progress_track_tqdm:
            progress(0, desc="Preparing train data...")

        unload_models()  # Need RAM for training

        prompter = Prompter(template)
        # variable_names = prompter.get_variable_names()

        data = get_data_from_input(
            load_dataset_from=load_dataset_from,
            dataset_text=dataset_text,
            dataset_text_format=dataset_text_format,
            dataset_plain_text_input_variables_separator=dataset_plain_text_input_variables_separator,
            dataset_plain_text_input_and_output_separator=dataset_plain_text_input_and_output_separator,
            dataset_plain_text_data_separator=dataset_plain_text_data_separator,
            dataset_from_data_dir=dataset_from_data_dir,
            prompter=prompter
        )

        train_data = prompter.get_train_data_from_dataset(data)

        data_count = len(train_data)

        def get_progress_text(epoch, epochs, last_loss):
            progress_detail = f"Epoch {math.ceil(epoch)}/{epochs}"
            if last_loss is not None:
                progress_detail += f", Loss: {last_loss:.4f}"
            return f"Training... ({progress_detail})"

        if Global.ui_dev_mode:
            Global.should_stop_training = False

            for i in range(300):
                if (Global.should_stop_training):
                    return
                epochs = 3
                epoch = i / 100
                last_loss = None
                if (i > 20):
                    last_loss = 3 + (i - 0) * (0.5 - 3) / (300 - 0)

                progress(
                    (i, 300),
                    desc="(Simulate) " +
                    get_progress_text(epoch, epochs, last_loss)
                )

                time.sleep(0.1)

            message = f"""Currently in UI dev mode, not doing the actual training.

Train options: {json.dumps({
    'max_seq_length': max_seq_length,
    'val_set_size': evaluate_data_count,
    'micro_batch_size': micro_batch_size,
    'gradient_accumulation_steps': gradient_accumulation_steps,
    'epochs': epochs,
    'learning_rate': learning_rate,
    'train_on_inputs': train_on_inputs,
    'lora_r': lora_r,
    'lora_alpha': lora_alpha,
    'lora_dropout': lora_dropout,
    'lora_target_modules': lora_target_modules,
    'model_name': model_name,
    'continue_from_model': continue_from_model,
    'continue_from_checkpoint': continue_from_checkpoint,
}, indent=2)}

Train data (first 10):
{json.dumps(train_data[:10], indent=2)}
            """
            print(message)
            time.sleep(2)
            return message

        if not should_training_progress_track_tqdm:
            progress(0, desc=f"Preparing model {base_model_name} for training...")

        log_history = []

        class UiTrainerCallback(TrainerCallback):
            def _on_progress(self, args, state, control):
                nonlocal log_history

                if Global.should_stop_training:
                    control.should_training_stop = True
                total_steps = (
                    state.max_steps if state.max_steps is not None else state.num_train_epochs * state.steps_per_epoch)
                log_history = state.log_history
                last_history = None
                last_loss = None
                if len(log_history) > 0:
                    last_history = log_history[-1]
                    last_loss = last_history.get('loss', None)

                progress_detail = f"Epoch {math.ceil(state.epoch)}/{epochs}"
                if last_loss is not None:
                    progress_detail += f", Loss: {last_loss:.4f}"

                progress(
                    (state.global_step, total_steps),
                    desc=f"Training... ({progress_detail})"
                )

            def on_epoch_begin(self, args, state, control, **kwargs):
                self._on_progress(args, state, control)

            def on_step_end(self, args, state, control, **kwargs):
                self._on_progress(args, state, control)

        training_callbacks = [UiTrainerCallback]

        Global.should_stop_training = False

        base_model = get_new_base_model(base_model_name)
        tokenizer = get_tokenizer(base_model_name)

        # Do not let other tqdm iterations interfere the progress reporting after training starts.
        # progress.track_tqdm = False  # setting this dynamically is not working, determining if track_tqdm should be enabled based on GPU cores at start instead.

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(os.path.join(output_dir, "info.json"), 'w') as info_json_file:
            dataset_name = "N/A (from text input)"
            if load_dataset_from == "Data Dir":
                dataset_name = dataset_from_data_dir

            info = {
                'base_model': base_model_name,
                'prompt_template': template,
                'dataset_name': dataset_name,
                'dataset_rows': len(train_data),
                'timestamp': time.time(),

                # These will be saved in another JSON file by the train function
                # 'max_seq_length': max_seq_length,
                # 'train_on_inputs': train_on_inputs,

                # 'micro_batch_size': micro_batch_size,
                # 'gradient_accumulation_steps': gradient_accumulation_steps,
                # 'epochs': epochs,
                # 'learning_rate': learning_rate,

                # 'evaluate_data_count': evaluate_data_count,

                # 'lora_r': lora_r,
                # 'lora_alpha': lora_alpha,
                # 'lora_dropout': lora_dropout,
                # 'lora_target_modules': lora_target_modules,
            }
            if continue_from_model:
                info['continued_from_model'] = continue_from_model
                if continue_from_checkpoint:
                    info['continued_from_checkpoint'] = continue_from_checkpoint
            json.dump(info, info_json_file, indent=2)

        if not should_training_progress_track_tqdm:
            progress(0, desc="Train starting...")

        wandb_group = template
        wandb_tags = [f"template:{template}"]
        if load_dataset_from == "Data Dir" and dataset_from_data_dir:
            wandb_group += f"/{dataset_from_data_dir}"
            wandb_tags.append(f"dataset:{dataset_from_data_dir}")

        train_output = Global.train_fn(
            base_model,  # base_model
            tokenizer,  # tokenizer
            output_dir,  # output_dir
            train_data,
            # 128,  # batch_size (is not used, use gradient_accumulation_steps instead)
            micro_batch_size,    # micro_batch_size
            gradient_accumulation_steps,
            epochs,   # num_epochs
            learning_rate,   # learning_rate
            max_seq_length,  # cutoff_len
            evaluate_data_count,  # val_set_size
            lora_r,  # lora_r
            lora_alpha,  # lora_alpha
            lora_dropout,  # lora_dropout
            lora_target_modules,  # lora_target_modules
            train_on_inputs,  # train_on_inputs
            False,  # group_by_length
            resume_from_checkpoint,  # resume_from_checkpoint
            save_steps,  # save_steps
            save_total_limit,  # save_total_limit
            logging_steps,  # logging_steps
            training_callbacks,  # callbacks
            Global.wandb_api_key,  # wandb_api_key
            Global.default_wandb_project if Global.enable_wandb else None,  # wandb_project
            wandb_group,  # wandb_group
            model_name,  # wandb_run_name
            wandb_tags  # wandb_tags
        )

        logs_str = "\n".join([json.dumps(log)
                             for log in log_history]) or "None"

        result_message = f"Training ended:\n{str(train_output)}\n\nLogs:\n{logs_str}"
        print(result_message)

        del base_model
        del tokenizer
        clear_cache()

        return result_message

    except Exception as e:
        raise gr.Error(
            f"{e} (To dismiss this error, click the 'Abort' button)")


def do_abort_training():
    Global.should_stop_training = True


def handle_continue_from_model_change(model_name):
    try:
        lora_models_directory_path = os.path.join(
            Global.data_dir, "lora_models")
        lora_model_directory_path = os.path.join(
            lora_models_directory_path, model_name)
        all_files = os.listdir(lora_model_directory_path)
        checkpoints = [
            file for file in all_files if file.startswith("checkpoint-")]
        checkpoints = ["-"] + checkpoints
        can_load_params = "finetune_params.json" in all_files or "finetune_args.json" in all_files
        return gr.Dropdown.update(choices=checkpoints, value="-"), gr.Button.update(visible=can_load_params), gr.Markdown.update(value="", visible=False)
    except Exception:
        pass
    return gr.Dropdown.update(choices=["-"], value="-"), gr.Button.update(visible=False), gr.Markdown.update(value="", visible=False)


def handle_load_params_from_model(
    model_name,
    max_seq_length,
    evaluate_data_count,
    micro_batch_size,
    gradient_accumulation_steps,
    epochs,
    learning_rate,
    train_on_inputs,
    lora_r,
    lora_alpha,
    lora_dropout,
    lora_target_modules,
    save_steps,
    save_total_limit,
    logging_steps,
    lora_target_module_choices,
):
    error_message = ""
    notice_message = ""
    unknown_keys = []
    try:
        lora_models_directory_path = os.path.join(
            Global.data_dir, "lora_models")
        lora_model_directory_path = os.path.join(
            lora_models_directory_path, model_name)

        data = {}
        possible_files = ["finetune_params.json", "finetune_args.json"]
        for file in possible_files:
            try:
                with open(os.path.join(lora_model_directory_path, file), "r") as f:
                    data = json.load(f)
            except FileNotFoundError:
                pass

        for key, value in data.items():
            if key == "max_seq_length":
                max_seq_length = value
            if key == "cutoff_len":
                cutoff_len = value
            elif key == "evaluate_data_count":
                evaluate_data_count = value
            elif key == "val_set_size":
                evaluate_data_count = value
            elif key == "micro_batch_size":
                micro_batch_size = value
            elif key == "gradient_accumulation_steps":
                gradient_accumulation_steps = value
            elif key == "epochs":
                epochs = value
            elif key == "num_train_epochs":
                epochs = value
            elif key == "learning_rate":
                learning_rate = value
            elif key == "train_on_inputs":
                train_on_inputs = value
            elif key == "lora_r":
                lora_r = value
            elif key == "lora_alpha":
                lora_alpha = value
            elif key == "lora_dropout":
                lora_dropout = value
            elif key == "lora_target_modules":
                lora_target_modules = value
                for element in value:
                    if element not in lora_target_module_choices:
                        lora_target_module_choices.append(element)
            elif key == "save_steps":
                save_steps = value
            elif key == "save_total_limit":
                save_total_limit = value
            elif key == "logging_steps":
                logging_steps = value
            elif key == "group_by_length":
                pass
            elif key == "resume_from_checkpoint":
                pass
            else:
                unknown_keys.append(key)
    except Exception as e:
        error_message = str(e)

    if len(unknown_keys) > 0:
        notice_message = f"Note: cannot restore unknown arg: {', '.join([f'`{x}`' for x in unknown_keys])}"

    message = ". ".join([x for x in [error_message, notice_message] if x])

    has_message = False
    if message:
        message += "."
        has_message = True

    return (
        gr.Markdown.update(value=message, visible=has_message),
        max_seq_length,
        evaluate_data_count,
        micro_batch_size,
        gradient_accumulation_steps,
        epochs,
        learning_rate,
        train_on_inputs,
        lora_r,
        lora_alpha,
        lora_dropout,
        gr.CheckboxGroup.update(value=lora_target_modules, choices=lora_target_module_choices),
        save_steps,
        save_total_limit,
        logging_steps,
        lora_target_module_choices,
    )


default_lora_target_module_choices = ["q_proj", "k_proj", "v_proj", "o_proj"]


def handle_lora_target_modules_add(choices, new_module, selected_modules):
    choices.append(new_module)
    selected_modules.append(new_module)

    return (choices, "", gr.CheckboxGroup.update(value=selected_modules, choices=choices))


def finetune_ui():
    things_that_might_timeout = []

    with gr.Blocks() as finetune_ui_blocks:
        with gr.Column(elem_id="finetune_ui_content"):
            with gr.Tab("Prepare"):
                with gr.Box(elem_id="finetune_ui_select_dataset_source"):
                    with gr.Row():
                        template = gr.Dropdown(
                            label="Template",
                            elem_id="finetune_template",
                        )
                        load_dataset_from = gr.Radio(
                            ["Text Input", "Data Dir"],
                            label="Load Dataset From",
                            value="Text Input",
                            elem_id="finetune_load_dataset_from")
                        reload_selections_button = gr.Button(
                            "↻",
                            elem_id="finetune_reload_selections_button"
                        )
                        reload_selections_button.style(
                            full_width=False,
                            size="sm")
                    with gr.Column(
                        elem_id="finetune_dataset_from_data_dir_group",
                        visible=False
                    ) as dataset_from_data_dir_group:
                        dataset_from_data_dir = gr.Dropdown(
                            label="Dataset",
                            elem_id="finetune_dataset_from_data_dir",
                        )
                        dataset_from_data_dir_message = gr.Markdown(
                            "",
                            visible=False,
                            elem_id="finetune_dataset_from_data_dir_message")
                with gr.Box(elem_id="finetune_dataset_text_input_group") as dataset_text_input_group:
                    gr.Textbox(
                        label="Training Data", elem_classes="textbox_that_is_only_used_to_display_a_label")
                    dataset_text = gr.Code(
                        show_label=False,
                        language="json",
                        value=sample_plain_text_value,
                        elem_id="finetune_dataset_text_input_textbox")
                    dataset_from_text_message = gr.Markdown(
                        "",
                        visible=False,
                        elem_id="finetune_dataset_from_text_message")
                    gr.Markdown(
                        "The data you entered here will not be saved. Do not make edits here directly. Instead, edit the data elsewhere then paste it here.")
                    with gr.Row():
                        with gr.Column():
                            dataset_text_format = gr.Radio(
                                ["Plain Text", "JSON Lines", "JSON"],
                                label="Format", value="Plain Text", elem_id="finetune_dataset_text_format")
                            dataset_text_load_sample_button = gr.Button(
                                "Load Sample", elem_id="finetune_dataset_text_load_sample_button")
                            dataset_text_load_sample_button.style(
                                full_width=False,
                                size="sm")
                        with gr.Column(elem_id="finetune_dataset_plain_text_separators_group") as dataset_plain_text_separators_group:
                            dataset_plain_text_input_variables_separator = gr.Textbox(
                                label="Input Variables Separator",
                                elem_id="dataset_plain_text_input_variables_separator",
                                placeholder=default_dataset_plain_text_input_variables_separator,
                                value=default_dataset_plain_text_input_variables_separator)
                            dataset_plain_text_input_and_output_separator = gr.Textbox(
                                label="Input and Output Separator",
                                elem_id="dataset_plain_text_input_and_output_separator",
                                placeholder=default_dataset_plain_text_input_and_output_separator,
                                value=default_dataset_plain_text_input_and_output_separator)
                            dataset_plain_text_data_separator = gr.Textbox(
                                label="Data Separator",
                                elem_id="dataset_plain_text_data_separator",
                                placeholder=default_dataset_plain_text_data_separator,
                                value=default_dataset_plain_text_data_separator)
                        things_that_might_timeout.append(
                            dataset_text_format.change(fn=handle_switch_dataset_text_format, inputs=[
                                dataset_text_format], outputs=[dataset_plain_text_separators_group]))

                    things_that_might_timeout.append(
                        dataset_text_load_sample_button.click(fn=load_sample_dataset_to_text_input, inputs=[
                            dataset_text_format], outputs=[dataset_text]))
                gr.Markdown(
                    "💡 Switch to the \"Preview\" tab to verify that your inputs are correct.")
            with gr.Tab("Preview"):
                with gr.Row():
                    finetune_dataset_preview_info_message = gr.Markdown(
                        "Set the dataset in the \"Prepare\" tab, then preview it here.",
                        elem_id="finetune_dataset_preview_info_message"
                    )
                    finetune_dataset_preview_count = gr.Number(
                        label="Preview items count",
                        value=10,
                        # minimum=1,
                        # maximum=100,
                        precision=0,
                        elem_id="finetune_dataset_preview_count"
                    )
                finetune_dataset_preview = gr.Dataframe(
                    wrap=True, elem_id="finetune_dataset_preview")
            things_that_might_timeout.append(
                load_dataset_from.change(
                    fn=handle_switch_dataset_source,
                    inputs=[load_dataset_from],
                    outputs=[
                        dataset_text_input_group,
                        dataset_from_data_dir_group
                    ]
                ))

            dataset_inputs = [
                template,
                load_dataset_from,
                dataset_from_data_dir,
                dataset_text,
                dataset_text_format,
                dataset_plain_text_input_variables_separator,
                dataset_plain_text_input_and_output_separator,
                dataset_plain_text_data_separator,
            ]
            dataset_preview_inputs = dataset_inputs + \
                [finetune_dataset_preview_count]

            with gr.Row():
                max_seq_length = gr.Slider(
                    minimum=1, maximum=4096, value=512,
                    label="Max Sequence Length",
                    info="The maximum length of each sample text sequence. Sequences longer than this will be truncated.",
                    elem_id="finetune_max_seq_length"
                )

                train_on_inputs = gr.Checkbox(
                    label="Train on Inputs",
                    value=True,
                    info="If not enabled, inputs will be masked out in loss.",
                    elem_id="finetune_train_on_inputs"
                )

        with gr.Row():
            # https://huggingface.co/docs/transformers/main/main_classes/trainer

            micro_batch_size_default_value = 1

            if Global.gpu_total_cores is not None and Global.gpu_total_memory is not None:
                memory_per_core = Global.gpu_total_memory / Global.gpu_total_cores
                if memory_per_core >= 6291456:
                    micro_batch_size_default_value = 8
                elif memory_per_core >= 4000000:  # ?
                    micro_batch_size_default_value = 4

            with gr.Column():
                micro_batch_size = gr.Slider(
                    minimum=1, maximum=100, step=1, value=micro_batch_size_default_value,
                    label="Micro Batch Size",
                    info="The number of examples in each mini-batch for gradient computation. A smaller micro_batch_size reduces memory usage but may increase training time."
                )

                gradient_accumulation_steps = gr.Slider(
                    minimum=1, maximum=10, step=1, value=1,
                    label="Gradient Accumulation Steps",
                    info="The number of steps to accumulate gradients before updating model parameters. This can be used to simulate a larger effective batch size without increasing memory usage."
                )

                epochs = gr.Slider(
                    minimum=1, maximum=100, step=1, value=10,
                    label="Epochs",
                    info="The number of times to iterate over the entire training dataset. A larger number of epochs may improve model performance but also increase the risk of overfitting.")

                learning_rate = gr.Slider(
                    minimum=0.00001, maximum=0.01, value=3e-4,
                    label="Learning Rate",
                    info="The initial learning rate for the optimizer. A higher learning rate may speed up convergence but also cause instability or divergence. A lower learning rate may require more steps to reach optimal performance but also avoid overshooting or oscillating around local minima."
                )

                evaluate_data_count = gr.Slider(
                    minimum=0, maximum=1, step=1, value=0,
                    label="Evaluation Data Count",
                    info="The number of data to be used for evaluation. This specific amount of data will be randomly chosen from the training dataset for evaluating the model's performance during the process, without contributing to the actual training.",
                    elem_id="finetune_evaluate_data_count"
                )

                with gr.Box(elem_id="finetune_continue_from_model_box"):
                    with gr.Row():
                        continue_from_model = gr.Dropdown(
                            value="-",
                            label="Continue from Model",
                            choices=["-"],
                            elem_id="finetune_continue_from_model"
                        )
                        continue_from_checkpoint = gr.Dropdown(
                            value="-",
                            label="Resume from Checkpoint",
                            choices=["-"],
                            elem_id="finetune_continue_from_checkpoint")
                    with gr.Column():
                        load_params_from_model_btn = gr.Button(
                            "Load training parameters from selected model", visible=False)
                        load_params_from_model_btn.style(
                            full_width=False,
                            size="sm")
                        load_params_from_model_message = gr.Markdown(
                            "", visible=False)

                    things_that_might_timeout.append(
                        continue_from_model.change(
                            fn=handle_continue_from_model_change,
                            inputs=[continue_from_model],
                            outputs=[
                                continue_from_checkpoint,
                                load_params_from_model_btn,
                                load_params_from_model_message
                            ]
                        )
                    )

            with gr.Column():
                lora_r = gr.Slider(
                    minimum=1, maximum=16, step=1, value=8,
                    label="LoRA R",
                    info="The rank parameter for LoRA, which controls the dimensionality of the rank decomposition matrices. A larger lora_r increases the expressiveness and flexibility of LoRA but also increases the number of trainable parameters and memory usage."
                )

                lora_alpha = gr.Slider(
                    minimum=1, maximum=128, step=1, value=16,
                    label="LoRA Alpha",
                    info="The scaling parameter for LoRA, which controls how much LoRA affects the original pre-trained model weights. A larger lora_alpha amplifies the impact of LoRA but may also distort or override the pre-trained knowledge."
                )

                lora_dropout = gr.Slider(
                    minimum=0, maximum=1, value=0.05,
                    label="LoRA Dropout",
                    info="The dropout probability for LoRA, which controls the fraction of LoRA parameters that are set to zero during training. A larger lora_dropout increases the regularization effect of LoRA but also increases the risk of underfitting."
                )

                lora_target_modules = gr.CheckboxGroup(
                    label="LoRA Target Modules",
                    choices=default_lora_target_module_choices,
                    value=["q_proj", "v_proj"],
                    info="Modules to replace with LoRA.",
                    elem_id="finetune_lora_target_modules"
                )
                lora_target_module_choices = gr.State(value=default_lora_target_module_choices)
                with gr.Box(elem_id="finetune_lora_target_modules_add_box"):
                    with gr.Row():
                        lora_target_modules_add = gr.Textbox(
                            lines=1, max_lines=1, show_label=False,
                            elem_id="finetune_lora_target_modules_add"
                        )
                        lora_target_modules_add_btn = gr.Button(
                            "Add",
                            elem_id="finetune_lora_target_modules_add_btn"
                        )
                        lora_target_modules_add_btn.style(full_width=False, size="sm")
                things_that_might_timeout.append(lora_target_modules_add_btn.click(
                    handle_lora_target_modules_add,
                    inputs=[lora_target_module_choices, lora_target_modules_add, lora_target_modules],
                    outputs=[lora_target_module_choices, lora_target_modules_add, lora_target_modules],
                ))

                with gr.Row():
                    logging_steps = gr.Number(
                        label="Logging Steps",
                        precision=0,
                        value=10,
                        elem_id="finetune_logging_steps"
                    )
                    save_steps = gr.Number(
                        label="Steps Per Save",
                        precision=0,
                        value=500,
                        elem_id="finetune_save_steps"
                    )
                    save_total_limit = gr.Number(
                        label="Saved Checkpoints Limit",
                        precision=0,
                        value=5,
                        elem_id="finetune_save_total_limit"
                    )

                with gr.Column():
                    model_name = gr.Textbox(
                        lines=1, label="LoRA Model Name", value=random_name,
                        max_lines=1,
                        info="The name of the new LoRA model.",
                        elem_id="finetune_model_name",
                    )

                    with gr.Row():
                        train_btn = gr.Button(
                            "Train", variant="primary", label="Train",
                            elem_id="finetune_start_btn"
                        )

                        abort_button = gr.Button(
                            "Abort", label="Abort",
                            elem_id="finetune_stop_btn"
                        )
                        confirm_abort_button = gr.Button(
                            "Confirm Abort", label="Confirm Abort", variant="stop",
                            elem_id="finetune_confirm_stop_btn"
                        )

        things_that_might_timeout.append(reload_selections_button.click(
            reload_selections,
            inputs=[template, dataset_from_data_dir],
            outputs=[template, dataset_from_data_dir, continue_from_model],
        ))

        for i in dataset_preview_inputs:
            things_that_might_timeout.append(
                i.change(
                    fn=refresh_preview,
                    inputs=dataset_preview_inputs,
                    outputs=[
                        finetune_dataset_preview,
                        finetune_dataset_preview_info_message,
                        dataset_from_text_message,
                        dataset_from_data_dir_message
                    ]
                ).then(
                    fn=refresh_dataset_items_count,
                    inputs=dataset_preview_inputs,
                    outputs=[
                        finetune_dataset_preview_info_message,
                        dataset_from_text_message,
                        dataset_from_data_dir_message,
                        evaluate_data_count,
                    ]
                ))

        finetune_args = [
            max_seq_length,
            evaluate_data_count,
            micro_batch_size,
            gradient_accumulation_steps,
            epochs,
            learning_rate,
            train_on_inputs,
            lora_r,
            lora_alpha,
            lora_dropout,
            lora_target_modules,
            save_steps,
            save_total_limit,
            logging_steps,
        ]

        things_that_might_timeout.append(
            load_params_from_model_btn.click(
                fn=handle_load_params_from_model,
                inputs=[continue_from_model] + finetune_args + [lora_target_module_choices],
                outputs=[load_params_from_model_message] + finetune_args + [lora_target_module_choices]
            )
        )

        train_output = gr.Text(
            "Training results will be shown here.",
            label="Train Output",
            elem_id="finetune_training_status")

        train_progress = train_btn.click(
            fn=do_train,
            inputs=(dataset_inputs + finetune_args + [
                model_name,
                continue_from_model,
                continue_from_checkpoint,
            ]),
            outputs=train_output
        )

        # controlled by JS, shows the confirm_abort_button
        abort_button.click(None, None, None, None)
        confirm_abort_button.click(
            fn=do_abort_training,
            inputs=None, outputs=None,
            cancels=[train_progress])

        stop_timeoutable_btn = gr.Button(
            "stop not-responding elements",
            elem_id="inference_stop_timeoutable_btn",
            elem_classes="foot_stop_timeoutable_btn")
        stop_timeoutable_btn.click(
            fn=None, inputs=None, outputs=None, cancels=things_that_might_timeout)

    finetune_ui_blocks.load(_js="""
    function finetune_ui_blocks_js() {
      // Auto load options
      setTimeout(function () {
        document.getElementById('finetune_reload_selections_button').click();
      }, 100);

      // Add tooltips
      setTimeout(function () {
        tippy('#finetune_reload_selections_button', {
          placement: 'bottom-end',
          delay: [500, 0],
          animation: 'scale-subtle',
          content: 'Press to reload options.',
        });

        tippy('#finetune_template', {
          placement: 'bottom-start',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Select a template for your prompt. <br />To see how the selected template work, select the "Preview" tab and then check "Show actual prompt". <br />Templates are loaded from the "templates" folder of your data directory.',
          allowHTML: true,
        });

        tippy('#finetune_load_dataset_from', {
          placement: 'bottom-start',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            '<strong>Text Input</strong>: Paste the dataset directly in the UI.<br/><strong>Data Dir</strong>: Select a dataset in the data directory.',
          allowHTML: true,
        });

        tippy('#finetune_dataset_preview_show_actual_prompt', {
          placement: 'bottom-start',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Check to show the prompt that will be feed to the language model.',
        });

        tippy('#dataset_plain_text_input_variables_separator', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Define a separator to separate input variables. Use "\\\\n" for new lines.',
        });

        tippy('#dataset_plain_text_input_and_output_separator', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Define a separator to separate the input (prompt) and the output (completion). Use "\\\\n" for new lines.',
        });

        tippy('#dataset_plain_text_data_separator', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Define a separator to separate different rows of the train data. Use "\\\\n" for new lines.',
        });

        tippy('#finetune_dataset_text_load_sample_button', {
          placement: 'bottom-start',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Press to load a sample dataset of the current selected format into the textbox.',
        });

        tippy('#finetune_evaluate_data_count', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'While setting a value larger than 0, the checkpoint with the lowest loss on the evaluation data will be saved as the final trained model, thereby helping to prevent overfitting.',
        });

        tippy('#finetune_save_total_limit', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Total amount of checkpoints to preserve. Older checkpoints will be deleted.',
        });
        tippy('#finetune_save_steps', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Number of updates steps before two checkpoint saves.',
        });
        tippy('#finetune_logging_steps', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Number of update steps between two logs.',
        });

        tippy('#finetune_model_name', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'The name of the new LoRA model. Must be unique.',
        });

        tippy('#finetune_continue_from_model', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'Select a LoRA model to train a new model on top of that model.<br /><br />💡 To use the same training parameters of a previously trained model, select it here and click the <code>Load training parameters from selected model</code> button, then un-select it.',
          allowHTML: true,
        });

        tippy('#finetune_continue_from_checkpoint', {
          placement: 'bottom',
          delay: [500, 0],
          animation: 'scale-subtle',
          content:
            'If a checkpoint is selected, training will resume from that specific checkpoint, bypassing any previously completed steps up to the checkpoint\\'s moment. <br /><br />💡 Use this option to resume an unfinished training session. Remember to click the <code>Load training parameters from selected model</code> button and select the same dataset for training.',
          allowHTML: true,
        });
      }, 100);

      // Show/hide start and stop button base on the state.
      setTimeout(function () {
        // Make the '#finetune_training_status > .wrap' element appear
        if (!document.querySelector('#finetune_training_status > .wrap')) {
          document.getElementById('finetune_confirm_stop_btn').click();
        }

        setTimeout(function () {
          let resetStopButtonTimer;
          document
            .getElementById('finetune_stop_btn')
            .addEventListener('click', function () {
              if (resetStopButtonTimer) clearTimeout(resetStopButtonTimer);
              resetStopButtonTimer = setTimeout(function () {
                document.getElementById('finetune_stop_btn').style.display = 'block';
                document.getElementById('finetune_confirm_stop_btn').style.display =
                  'none';
              }, 5000);
              document.getElementById('finetune_confirm_stop_btn').style['pointer-events'] =
                'none';
              setTimeout(function () {
                document.getElementById('finetune_confirm_stop_btn').style['pointer-events'] =
                  'inherit';
              }, 300);
              document.getElementById('finetune_stop_btn').style.display = 'none';
              document.getElementById('finetune_confirm_stop_btn').style.display =
                'block';
            });
          const output_wrap_element = document.querySelector(
            '#finetune_training_status > .wrap'
          );
          function handle_output_wrap_element_class_change() {
            if (Array.from(output_wrap_element.classList).includes('hide')) {
              if (resetStopButtonTimer) clearTimeout(resetStopButtonTimer);
              document.getElementById('finetune_start_btn').style.display = 'block';
              document.getElementById('finetune_stop_btn').style.display = 'none';
              document.getElementById('finetune_confirm_stop_btn').style.display =
                'none';
            } else {
              document.getElementById('finetune_start_btn').style.display = 'none';
              document.getElementById('finetune_stop_btn').style.display = 'block';
              document.getElementById('finetune_confirm_stop_btn').style.display =
                'none';
            }
          }
          new MutationObserver(function (mutationsList, observer) {
            handle_output_wrap_element_class_change();
          }).observe(output_wrap_element, {
            attributes: true,
            attributeFilter: ['class'],
          });
          handle_output_wrap_element_class_change();
        }, 500);
      }, 0);
    }
    """)


def get_val_from_arr(arr, index, default=None):
    return arr[index] if -len(arr) <= index < len(arr) else default


default_dataset_plain_text_input_variables_separator = "\\n-\\n"
default_dataset_plain_text_input_and_output_separator = "\\n/\\n"
default_dataset_plain_text_data_separator = "\\n####\\n"

sample_plain_text_value = """
Find the capital of Spain.
/
The capital of Spain is Madrid.
####
Rewrite the following sentence using active voice.
-
The news report was read by the captain.
/
The captain read the news report.
####
Classify the following into animals, plants, and minerals.
-
Oak tree, copper ore, elephant
/
Oak tree: Plant
Copper ore: Mineral
Elephant: Animal
"""

sample_jsonl_text_value = """
{"id": "seed_task_0", "name": "breakfast_suggestion", "instruction": "Is there anything I can eat for a breakfast that doesn't include eggs, yet includes protein, and has roughly 700-1000 calories?", "instances": [{"input": "", "output": "Yes, you can have 1 oatmeal banana protein shake and 4 strips of bacon. The oatmeal banana protein shake may contain 1/2 cup oatmeal, 60 grams whey protein powder, 1/2 medium banana, 1tbsp flaxseed oil and 1/2 cup watter, totalling about 550 calories. The 4 strips of bacon contains about 200 calories."}], "is_classification": false}
{"id": "seed_task_1", "name": "antonym_relation", "instruction": "What is the relation between the given pairs?", "instances": [{"input": "Night : Day :: Right : Left", "output": "The relation between the given pairs is that they are opposites."}], "is_classification": false}
{"id": "seed_task_2", "name": "one_sentence_description", "instruction": "Generate a one-sentence description for each of the following people.", "instances": [{"input": "- Brack Obama\\n- Elon Musk\\n- Taylor Swift", "output": "- Barack Hussein Obama II is an American politician who served as the 44th president of the United States from 2009 to 2017.\\n- Elon Musk is the founder, CEO, and chief engineer of SpaceX; angel investor, CEO and product architect of Tesla, Inc.; founder of The Boring Company; co-founder of Neuralink and OpenAI; president of the Musk Foundation; and owner and CEO of Twitter, Inc.\\n- Taylor Alison Swift is an American singer-songwriter."}], "is_classification": false}
{"id": "seed_task_3", "name": "harmful_stereotype_example", "instruction": "Describe a situation in which the given stereotype can harm you.", "instances": [{"input": "\\"All Asians are smart!\\"", "output": "Imagine you're a small child of Asian descent. You hear your whole life how \\"all Asians are smart,\\" but you know that does not feel like it's true for you. You have a hard time in school. Reading is difficult. Math is impossible. Everyone around you seems to pressure you because you are supposed to be \\"smart.\\" Every time you do not bring home that \\"A\\" assignment, you feel like a failure. Like you alone missed out on the gene that makes \\"all Asians smart.\\"\\nAs educational opportunities like scholarships and colleges close their door on you, your future looks bleak. You have spent years trying to be the stereotypically smart Asian instead of finding the talents that could have taken you to success in other areas. As you can see, although the stereotype doesn't sound like a bad thing at first, it can put a lot of weight on the people who do not fit into it."}], "is_classification": false}
{"id": "seed_task_4", "name": "email_subject_generation", "instruction": "Generate an appropriate subjective title for the following email:", "instances": [{"input": "Hi [person name],\\n\\nI'm writing to ask you if you are happy to be a panelist of our workshop on multimodality at CVPR. The workshop will be held on June 20, 2023. \\n\\nBest,\\n[my name]", "output": "Invitition to be a panelist for CVPR 2023 workshop on Multimodality"}], "is_classification": false}
{"id": "seed_task_5", "name": "interview_question_answering", "instruction": "How do you answer this question in a job interview?", "instances": [{"input": "What do you think about when you are alone in a car?", "output": "If it is in the evening, I typically think about what good I have accomplished that day, and if it is in the morning, I think about how I can be my best that day. I also try to practice gratitude and live in the present moment while I'm driving to avoid distractions."}], "is_classification": false}
{"id": "seed_task_6", "name": "new_year's_resolutions", "instruction": "Brainstorm a list of possible New Year's resolutions.", "instances": [{"input": "", "output": "- Lose weight\\n- Exercise more\\n- Eat healthier\\n- Quit smoking\\n- Drink less alcohol\\n- Spend less money\\n- Save more money\\n- Get out of debt\\n- Take a trip\\n- Volunteer more\\n- Spend more time with family and friends\\n- Read more\\n- Start a hobby\\n- Get organized\\n- Simplify your life\\n- Meditate or pray more\\n- Get a new job\\n- Move to a new city\\n- Make new friends\\n- Go back to school\\n- Learn a new language\\n- Start a business"}], "is_classification": false}
{"id": "seed_task_7", "name": "idiom_meaning", "instruction": "Explain the following idiom to me, and try to give me some examples.", "instances": [{"input": "black sheep", "output": "Meaning: An outcast. Someone who doesn\\u2019t fit in with the rest of the crowd. They take pride in being different. Thinks for themselves and doesn\\u2019t care what no one else has to say. They tend to ride their own wave and are usually loners because no one understands them, but its okay because they like it that way.\\nExample: He\\u2019s the black sheep of the family."}], "is_classification": false}
{"id": "seed_task_8", "name": "english_haiku_generation", "instruction": "Generate a haiku using the following word:", "instances": [{"input": "summer", "output": "The chill, worming in\\nShock, pleasure, bursting within\\nSummer tongue awakes"}], "is_classification": false}
{"id": "seed_task_9", "name": "movie_recommendation", "instruction": "Recommend a movie for me to watch during the weekend and explain the reason.", "instances": [{"input": "", "output": "I would recommend the movie \\"The Shawshank Redemption\\" because it is an excellent movie that is both moving and inspiring. It is the story of a man who is unjustly imprisoned and his struggle to maintain hope and dignity. It is a great film to watch over the weekend because it will make you think about the human capacity for resilience and hope."}], "is_classification": false}
{"id": "seed_task_10", "name": "game_suggestion", "instruction": "Suggest some games that can be played by a group of people.", "instances": [{"input": "", "output": "1. Werewolf 2. Who Am I?  3. Charades 4. Balderdash 5. Pictionary 6. Two Truths and a Lie 7. Uno 8. Story Starters 9. Truth or Dare 10. Kubb"}], "is_classification": false}
{"id": "seed_task_11", "name": "grocery_list", "instruction": "Make a grocery list for a healthy meal.", "instances": [{"input": "", "output": "-Vegetables: carrots, celery, broccoli, cauliflower, spinach, kale, tomatoes, peppers, onions, garlic \\n-Fruit: apples, bananas, oranges, pears, grapes, berries \\n-Protein: chicken, fish, tofu, legumes, eggs \\n-Whole grains: quinoa, rice, oats, bread, pasta \\n-Healthy fats: olive oil, avocado, nuts, seeds \\n-Other: herbs, spices, salt, pepper, vinegar, lemon juice"}], "is_classification": false}
{"id": "seed_task_12", "name": "explain_behavior", "instruction": "Explain human's behavior.", "instances": [{"input": "Behavior: cry.", "output": "There could be many reasons why a person might cry. They could be feeling sad, scared, angry, or frustrated. Sometimes people cry when they are happy or relieved. There is no one answer to why people behave the way they do."}], "is_classification": false}
{"id": "seed_task_13", "name": "dependency_parsing", "instruction": "Dependency parsing for the sentence. Output the dependencies in lines, with each line formatted as (current word -> its head word).", "instances": [{"input": "I didn't cook dinner yesterday because I went out with my dear wife.", "output": "I -> cook\\ndid -> cook\\nn't -> cook\\ncook -> ROOT\\ndinner -> cook\\nyesterday -> cook\\nbecause -> went\\nI -> went\\nwent -> cook\\nout -> went\\nwith -> wife\\nmy -> wife\\ndear -> wife\\nwife -> went"}], "is_classification": false}
{"id": "seed_task_14", "name": "question_answering_generation_from_facts", "instruction": "Using the facts given, write a question-answer pair.", "instances": [{"input": "Name: Bill Gates\\nBorn: October 28, 1955 (age 66)\\nEducation: Harvard University (dropped out)\\nOccupation: Software developer, investor, entrepreneur\\nSource: Wikipedia\\nTime: August 2022", "output": "Question: What is Bill Gates' occupation? \\nAnswer: He is a software developer, investor, and entrepreneur."}], "is_classification": false}
{"id": "seed_task_15", "name": "analogy_completion", "instruction": "To make the pairs have the same analogy, write the fourth word.", "instances": [{"input": "Instability : Turmoil:: Change :", "output": "Instability : Turmoil :: Change : Revolution"}], "is_classification": false}
{"id": "seed_task_16", "name": "medicine_suggestion", "instruction": "Given a description of the symptom, identify the possible disease and suggest some medicine.", "instances": [{"input": "I have a fever and I am coughing.", "output": "The possible disease is pneumonia. You can probably try antibiotics but you should consult your doctor if it's serious."}], "is_classification": false}
{"id": "seed_task_17", "name": "birthday_planning_checklist", "instruction": "Create a birthday planning checklist.", "instances": [{"input": "", "output": "- Create a guest list\\n- Send out invitations\\n- Plan a menu\\n- Prepare decorations\\n- Plan activities\\n- Prepare party favors\\n- Plan for transportation and parking\\n- Select a birthday cake\\n- Make a reservation"}], "is_classification": false}
{"id": "seed_task_18", "name": "refuse_invitation_email", "instruction": "Reply to the email and refuse the invitation politely.", "instances": [{"input": "Here is the email. \\n\\nDear ARR Reviewer,\\nMany thanks for your continuous support of ARR. We are now looking for volunteers for emergency reviewers for the July 2022 cycle (the submission deadline was July 15, and the regular reviewing deadline is August 21).\\nEmergency reviewing will take place between August 21 and August 28. If you're available to provide one or more emergency reviews in this period (regardless of whether you had any regular reviewer assignments in this cycle or not), please fill in this sheet (providing your OR ID, area(s) of expertise, and the number of emergency reviews you can write).\\nMany thanks to all volunteers in advance!\\nBest regards, \\nARR EiCs", "output": "Hello, \\nThank you for reaching out. Unfortunately, I am not available during that period to provide emergency reviews. \\nBest, \\n[Name]"}], "is_classification": false}
{"id": "seed_task_19", "name": "explain_without-background", "instruction": "In your opinion, how would you explain the relation of the given subject to people who do not have a background in the field?", "instances": [{"input": "Machine Learning and Data Mining", "output": "They are two very closely related fields of study that deal with extracting information from data. Machine Learning is concerned with the development of algorithms that can learn from data and make predictions about new data. Data Mining is concerned with the extraction of patterns and trends from data. Both fields make use of a variety of techniques, including statistics, artificial intelligence, and pattern recognition."}], "is_classification": false}
{"id": "seed_task_20", "name": "horror_movie_opening", "instruction": "You need to write a creative opening scene for a horror movie.", "instances": [{"input": "", "output": "The sun had long since set, leaving the small town in darkness. A light breeze blew through the empty streets, sending a chill down the spine of anyone who dared to venture outside. The only sound was the soft rustle of leaves as they were blown around by the wind. Suddenly, a blood-curdling scream pierced the silence, followed by the sound of breaking glass. A light came on in one of the houses, and a figure could be seen running towards the center of town. As the figure got closer, it became clear that it was a young woman, and she was covered in blood."}], "is_classification": false}
{"id": "seed_task_21", "name": "fun_math_question", "instruction": "Create a fun math question for children.", "instances": [{"input": "", "output": "I am an odd number. Take away one letter and I become even. What number am I?"}], "is_classification": false}
{"id": "seed_task_22", "name": "find_subset_of_numbers_with_target_sum", "instruction": "Given a set of numbers, find all possible subsets that sum to a given number.", "instances": [{"input": "Set: {3,12,7,2,8,4}\\nTarget sum: 14", "output": "{12,2}, {7,3,4}, {8,2,4}"}], "is_classification": false}
{"id": "seed_task_23", "name": "cooking_recipe", "instruction": "Give me a cooking recipe for the following food.", "instances": [{"input": "Tteokbokki", "output": "Ingredients: 1/2 cup rice cakes, 1/2 cup fish cakes, 1/4 cup gochujang, 1/4 cup water, 1/4 cup sugar, 1/4 onion, 1 green onion.\\nInstructions: 1. In a pan, combine the rice cakes, fish cakes, gochujang, water, sugar, and onion. 2. Cook over medium heat for 5-7 minutes, stirring occasionally. 3. Add the green onion and cook for 1-2 minutes."}], "is_classification": false}
{"id": "seed_task_24", "name": "school_lunches", "instruction": "Plan a weekly lunch menu for a school. Write down a main dish, a carbohydrate side dish, a vegetable side dish, and a dessert for each day.", "instances": [{"input": "", "output": "Monday: \\nMain Dish: Chicken Teriyaki, Carbohydrate Side Dish: Fried Rice, Vegetable Side Dish: Broccoli, Dessert: Fruit Cup \\nTuesday: \\nMain Dish: Spaghetti and Meatballs, Carbohydrate Side Dish: Garlic Bread, Vegetable Side Dish: Green Beans, Dessert: Chocolate Cake \\nWednesday: \\nMain Dish: Beef Stew, Carbohydrate Side Dish: Biscuits, Vegetable Side Dish: carrots, Dessert: Jell-O \\nThursday:\\nMain Dish: Turkey and Cheese Sandwich,  Carbohydrate Side Dish: Potato Chips, Vegetable Side Dish: Lettuce and Tomato, Dessert: Yogurt \\nFriday: \\nMain Dish: Fish Sticks, Carbohydrate Side Dish: Tater Tots, Vegetable Side Dish: Peas, Dessert: Ice Cream"}], "is_classification": false}
{"id": "seed_task_25", "name": "perfect_numbers", "instruction": "Find the four smallest perfect numbers.", "instances": [{"input": "", "output": "6, 28, 496, and 8128"}], "is_classification": false}
{"id": "seed_task_26", "name": "entity_linking_to_wikipedia", "instruction": "Link all the entities in the sentence (highlighted in brackets) to a Wikipedia page. For each entity, you should output the Wikipedia page title, or output None if you don't know.", "instances": [{"input": "[Naruto] is a [Nippon] manga series written and illustrated by [Masashi]. It tells the story of a young ninja who seeks recognition from his peers and dreams of becoming the [Hokage], the leader of his village.", "output": "Naruto -> Naruto\\nNippon -> Japan\\nMasashi -> Masashi Kishimoto\\nHokage -> None"}], "is_classification": false}
{"id": "seed_task_27", "name": "biased_question", "instruction": "Are you smarter than most redheads?", "instances": [{"input": "", "output": "That's a mean question! I try my best to be fair to everyone!"}], "is_classification": false}
{"id": "seed_task_28", "name": "write_comments_in_python", "instruction": "Add comments in the given function to make the code more readable for humans.", "instances": [{"input": "def hill_climbing(initial_board):\\n    current = initial_board\\n    while current.has_successors():\\n        neighbour = Board(current.get_random_successor(), \\"best\\")\\n        if neighbour.value() >= current.value():\\n            return current\\n        current = neighbour\\n    return current", "output": "def hill_climbing(initial_board):\\n\\"\\"\\" Hill Climbing Algorithm.\\n\\nPerforms a hill climbing search on initial_board and returns a Board\\nobject representing a goal state (local/global minimum).\\n\\nAttributes:\\n    current: A Board object\\n    neighbour: A Board object that is a successor of current\\n\\n:param initial_board: A Board object with a randomly generated state, and successor_type of \\"best\\".\\n                      i.e. a start state\\n:return: A Board object that has no further successors. i.e. a goal state (Local/Global Minimum)\\n\\"\\"\\"\\n\\ncurrent = initial_board\\nwhile current.has_successors():\\n    neighbour = Board(current.get_random_successor(), \\"best\\")\\n    if neighbour.value() >= current.value():\\n        return current\\n    current = neighbour\\nreturn current"}], "is_classification": false}
{"id": "seed_task_29", "name": "story_composition", "instruction": "Given the facts, compose them into a coherent and fascinating story.", "instances": [{"input": "Facts: \\n- PersonX was born in South Africa.\\n- PersonX spent most of his happy childhood playing with his friends.\\n- PersonX started learning programming only after college. \\n- PersonX became a billionaire.", "output": "Entrepreneur PersonX has achieved great fame as the CEO of an international company. His company, the Dolphin, is one of the most profitable fintech companies in the US as of 2022. Despite his success in the fintech field, PersonX didn't start learning programming before his college. Born in a middle-class family in South Africa, PersonX spent most of his childhood hanging out with his friends, instead of playing on computers. \\"My dad didn't buy me a computer, so I had no idea about programming back then, \\" Person X told an interviewer. However, he really demonstrated his talent in programming after he went to college. He started his first startup in his second year, and that startup is later acquired by Microsoft."}], "is_classification": false}
{"id": "seed_task_30", "name": "snack_suggestion", "instruction": "Make a list of salty snacks with a soft texture.", "instances": [{"input": "", "output": "- String Cheese\\n- Salty Mashed Potatoes\\n- Hard-Boiled Egg\\n- Mac and Cheese"}], "is_classification": false}
{"id": "seed_task_31", "name": "complete_python_code", "instruction": "Suggest a completion for the following python code.", "instances": [{"input": "import json\\n\\ndef read_task_from_jsonl(data_file):\\n    '''This function will read a .jsonl file and return the ``task`` fields in all the lines.'''", "output": "import json\\n\\ndef read_task_from_jsonl(data_file):\\n    '''This function will read a .jsonl file and return the ``task`` fields in all the lines.'''\\n    with open(data_file) as fin:\\n        return [json.loads(line)['task'] for line in fin]"}], "is_classification": false}
{"id": "seed_task_32", "name": "exercise_explanation", "instruction": "Given the name of an exercise, explain how to do it.", "instances": [{"input": "Burpees", "output": "Starting with your feet shoulder-width apart, get down into a squat position. From a deep squat, put your hands down on the floor inside your feet and jump your feet back behind you, so you\\u2019re in a plank position, then do a push-up, touching your chest to the floor. Raise back up onto your arms in a plank, then jump your feet back in underneath your chest. Jump your feet outside your hands and as you stand up, jump up with your arms in the air. Aim for 10 reps."}], "is_classification": false}
{"id": "seed_task_33", "name": "food_and_cooking_survey", "instruction": "Create a survey that asks about eating and cooking habits.", "instances": [{"input": "", "output": "1. Do you enjoy cooking?\\n2. Do you like to eat out?\\n3. How often do you eat out?\\n4. What is your favorite food?\\n5. What is your favorite restaurant?\\n6. What is your favorite thing to cook at home?\\n7. Do you have any dietary restrictions?\\n8. Do you have any food allergies?"}], "is_classification": false}
{"id": "seed_task_34", "name": "suggestion_for_breaking_up", "instruction": "I broke up with my girlfriend and I am feeling so sad. Can you give me some suggestions?", "instances": [{"input": "", "output": "I am so sorry for that! But here are a few things that could help you to get out of it:\\n1. Spend time with friends and family who make you feel good about yourself. \\n2. Do things that make you happy. \\n3. Avoid dwelling on negative thoughts by keeping yourself busy with positive activities. \\n4. Talk to a therapist or counselor if you are struggling to cope with your sadness."}], "is_classification": false}
{"id": "seed_task_35", "name": "solving_equation", "instruction": "Solving the equation and find the value of X. Show your steps.", "instances": [{"input": "10X + 5 = 10", "output": "10X = 5\\nX = 0.5"}], "is_classification": false}
{"id": "seed_task_36", "name": "religion_adjectives", "instruction": "Write the adjectives that represent the given religions.", "instances": [{"input": "Atheism:\\nChristianity:\\nIslam:\\nHinduism:", "output": "Atheism: Atheistic, secular, doubter, irreligious, unbelieving.\\nChristianity: spiritual, devout, pious, God-fearing.\\nIslam: spiritual, devout, pious, God-fearing.\\nHinduism: spiritual, devout, pious, God-fearing."}], "is_classification": false}
{"id": "seed_task_37", "name": "code_todo", "instruction": "Using the comment as a guide, replace the \\"TODO\\" comment in the code with the proper code.", "instances": [{"input": "def greet_to(name):\\n    \\"\\"\\"Print a greeting to the name provided.\\"\\"\\"\\n    # TODO: assert name is a string\\n    print(\\"Hello, \\" + name + \\"!\\")", "output": "def greet_to(name):\\n    \\"\\"\\"Print a greeting to the name provided.\\"\\"\\"\\n    # assert name is a string\\n    assert isinstance(name, str), \\"name must be a string\\"\\n    print(\\"Hello, \\" + name + \\"!\\")"}], "is_classification": false}
{"id": "seed_task_38", "name": "synonym_generation", "instruction": "Come up with the synonyms of the word.", "instances": [{"input": "surprisingly", "output": "amazingly, astonishingly, unexpectedly, incredibly, unbelievably"}], "is_classification": false}
{"id": "seed_task_39", "name": "extract_country_names", "instruction": "Extract all the country names in the paragraph, and list them separated by commas.", "instances": [{"input": "Dr. No is the sixth novel by the English author Ian Fleming to feature his British Secret Service agent James Bond. Written at Fleming's Goldeneye estate in Jamaica, it was first published in the United Kingdom by Jonathan Cape in 1958. In the novel Bond looks into the disappearance in Jamaica of two fellow MI6 operatives who had been investigating Doctor No. Bond travels to No's Caribbean island and meets Honeychile Rider, who is there to collect shells. They are captured and taken to a luxurious facility carved into a mountain. The character of Doctor No, the son of a German missionary and a Chinese woman, was influenced by Sax Rohmer's Fu Manchu stories. Dr. No was the first of Fleming's novels to face widespread negative reviews in Britain, but it was received more favourably in the United States. The story was adapted in 1962 as the first film in the Bond series, with Sean Connery in the lead role.", "output": "English, British, Jamaica, the United Kingdom, German, Chinese, Britain, the United States."}], "is_classification": false}
{"id": "seed_task_40", "name": "plan_a_trip", "instruction": "I am planning a 7-day trip to Seattle. Can you make a detailed plan for me?", "instances": [{"input": "", "output": "Day 1: Arrive in Seattle. Check into your hotel and explore the city.\\nDay 2: Visit the Space Needle, Pike Place Market, and the Seattle Aquarium.\\nDay 3: Take a ferry to Bainbridge Island and explore the island.\\nDay 4: Visit the Museum of Flight, the Seattle Art Museum, and the Seattle Symphony.\\nDay 5: Take a day trip to Mount Rainier National Park.\\nDay 6: Visit the Woodland Park Zoo, the Hiram M. Chittenden Locks, and the Seattle Japanese Garden.\\nDay 7: Depart Seattle."}], "is_classification": false}
{"id": "seed_task_41", "name": "regular_expression_generation", "instruction": "Generate the regular expression based on the description.", "instances": [{"input": "Description: matching strings that start with numbers and end with space.", "output": "^[0-9].*\\\\s$"}], "is_classification": false}
{"id": "seed_task_42", "name": "grammar_error_correction", "instruction": "Tell me if the sentence is grammatical. If it is, output yes. Otherwise, correct the grammar errors.", "instances": [{"input": "John visited China after his graduation from Yale university, where he really enjoyed the food and talking with local people.", "output": "No, it is not grammatical. Here is the correct version: John visited China after graduating from Yale University. He really enjoyed the food and talking with locals."}], "is_classification": false}
{"id": "seed_task_43", "name": "correct_misspelling", "instruction": "Find the misspelling in the sentence, and give me the correct spelling.", "instances": [{"input": "I didn't see her mother yesterday because I had to spend a lot of time in the hospical.", "output": "hospical -> hospital."}], "is_classification": false}
{"id": "seed_task_44", "name": "add_to_the_list", "instruction": "Add things to the list.", "instances": [{"input": "I think that in the summer, dogs might like to eat watermelon, frozen peanut butter, their regular food\\u2026", "output": "\\u2026 water from a hose, ice cubes, cold meat"}], "is_classification": false}
{"id": "seed_task_45", "name": "math_word_problem_with_reasoning", "instruction": "Answer the following math problem. You should write down the reasoning steps.", "instances": [{"input": "Roger has 5 tennis balls. He buys 2 more cans of tennis balls. Each can has 3 tennis balls. How many tennis balls does he have now?", "output": "Roger started with 5 balls. 2 cans of 3 tennis balls each is 6 tennis balls. 5 + 6 = 11. The answer is 11."}], "is_classification": false}
{"id": "seed_task_46", "name": "unethical_behavior_at_workplace", "instruction": "Write an example of unethical behavior at the workplace and explain why it is unethical.", "instances": [{"input": "", "output": "An example of unethical behavior is the failure to honor commitments. For example, \\nyour boss promises you an extra day off if you rush out an important project by a certain date. You work late hours and finish the project before the deadline. Ready for your day off, you mention it to your boss who responds \\"No, we have too much work to do.\\" Your boss engaged in unethical behavior that has virtually guaranteed your future distrust and unwillingness to extend yourself to assist in department emergencies. In addition, you are likely to complain to your co-workers, causing them to distrust the promises of the boss and be unwilling to cooperate with his requests."}], "is_classification": false}
{"id": "seed_task_47", "name": "fact_to_conversation", "instruction": "Write a conversation based on the given facts.", "instances": [{"input": "Facts: \\nTemp: 10 C, pm2.5: 27, Loc: Beijing, Roles: User and Bot", "output": "User: Hi there! I'm just checking in to see how the air quality is looking in Beijing today.\\nBot: According to our data, the air quality index is currently at 27, which is considered healthy. The temperature is 10 degrees Celsius."}], "is_classification": false}
{"id": "seed_task_48", "name": "question_answering", "instruction": "Answer the following question.", "instances": [{"input": "When did US land human on the moon?", "output": "July 20, 1969."}], "is_classification": false}
{"id": "seed_task_49", "name": "metaphor_for_a_situation", "instruction": "Come up with a metaphor that can describe my situation.", "instances": [{"input": "Situation: I fell in love with her.", "output": "I am like a moth to a flame; I can't help but be drawn to her."}], "is_classification": false}
{"id": "seed_task_50", "name": "text_to_sql", "instruction": "Parse the natural language query into a SQL query that can be executed on the table.", "instances": [{"input": "What are the name and budget of the departments with average instructor salary greater than the overall average?\\nGiven two tables: instructor, department.", "output": "SELECT d.name, d.budget FROM instructor i JOIN department d ON i.dept_name = d.name GROUP BY i.dept_name HAVING AVG(i.salary) > (SELECT AVG(salary) FROM instructor)"}], "is_classification": false}
{"id": "seed_task_51", "name": "better_word_choice", "instruction": "In this task, you are given a sentence and a word or phrase from the sentence, you need to suggest a better word or phrase to replace it without changing the original meaning.", "instances": [{"input": "Sentence: I am very happy to be part of the team.\\nThe original words: very happy", "output": "The better words: thrilled, elated"}], "is_classification": false}
{"id": "seed_task_52", "name": "pre-run_warmup", "instruction": "Suggest a quick pre-run warmup routine. Explain each move briefly.", "instances": [{"input": "", "output": "1. Quad + Piriformis Walk: Start standing then draw your left foot up behind you, pulling toward your butt for a quad stretch. Release and step forward; switch legs. After 30 seconds, cradle right leg at ankle and knee, pulling up to the chest. Release and step forward; switch legs. Repeat for 30 seconds.\\n2. Hip Opener: From standing, bend right knee and lift knee to hip level, then rotate the knee out to 90 degrees. (Place hand over knee to stabilize and guide if needed.) Bring leg back to front; lower foot, and switch sides. Repeat for 30 seconds.\\n3. Arm Circles: Stand with feet shoulder-width apart and lift arms out to shoulder height, palms down. Make small circles; after 30 seconds, switch direction. Continue for another 30 seconds.\\n4. Frankenstein Walk: Start standing with your feet together. Extend your right leg straight out in front of you as you bring your left hand to tap your right toes. Lower leg and step forward; repeat on the opposite side. Continue for 30 seconds.\\n5. Leg Crossover + Scorpion: Lie faceup, legs straight and arms out. Lift your right leg up and across your body, tapping your foot to the floor. Return to start; repeat on other side. After 30 seconds, flip over to lie facedown and perform a Scorpion. Lie facedown. Draw your left leg up and cross it over your body so that your left foot is nearly in line with your right hip. Hold for a breath or two, then return to start. Repeat on the other side and continue to alternate for 30 seconds.\\n6. Inchworm: From standing, bend forward at the waist to touch toes, then walk hands out to a high plank. Hold for 2 seconds; walk feet to meet hands. Roll up to starting position. Repeat for 1 minute."}], "is_classification": false}
{"id": "seed_task_53", "name": "word_location_in_text", "instruction": "Given a sentence and a number, return the word that correspond to the location of the given number in the sentence, where each word is a white-space separated and the location index starts from 1.", "instances": [{"input": "This is a random sentence. 4", "output": "random"}], "is_classification": false}
{"id": "seed_task_54", "name": "explain_social_stereotypes_of_professions.", "instruction": "Explain the social stereotype of the following profession.", "instances": [{"input": "lawyer", "output": "The social stereotype of lawyers is that they are intelligent and often have high income."}], "is_classification": false}
{"id": "seed_task_55", "name": "joke_about_phd", "instruction": "Give me a joke about PhD. Try to make a long joke.", "instances": [{"input": "", "output": "Why God never got a PhD?\\n1. He had only one major publication.\\n2. It was written in Aramaic, not in English.\\n3. It has no references.\\n4. It wasn't even published in a refereed journal.\\n5. There are serious doubts he wrote it himself.\\n6. It may be true that he created the world, but what has he done since\\nthen?\\n7. His cooperative efforts have been quite limited."}], "is_classification": false}
{"id": "seed_task_56", "name": "harry_potter_spells", "instruction": "Write some spells from the Harry Potter series.", "instances": [{"input": "", "output": "1. Accio: This spell summons an object to the caster's hand. \\n2. Alohomora: This spell opens locked doors. \\n3. Avada Kedavra: This spell kills the target. \\n4. Expelliarmus: This spell disarms an opponent. \\n5. Expecto Patronum: This spell creates a Patronus, which is a protective charm. \\n6. Legilimens: This spell allows the caster to read the thoughts of the target. \\n7. Lumos: This spell lights up the caster's wand tip."}], "is_classification": false}
{"id": "seed_task_57", "name": "sentence_with_specified_ending", "instruction": "Write a sentence that ends with the word \\"published\\".", "instances": [{"input": "", "output": "We spent a lot of time collecting the data and it finally got published."}], "is_classification": false}
{"id": "seed_task_58", "name": "measurement_conversion", "instruction": "Convert the following measurement.", "instances": [{"input": "3 ft = _____ in.", "output": "3 ft = 36 in."}], "is_classification": false}
{"id": "seed_task_59", "name": "three_sentence_story", "instruction": "Write a story that contains the given words in 4 sentences.", "instances": [{"input": "universe, whale, killer.", "output": "There once was a killer whale who ruled the universe. All the other animals were scared of him, except for one little bird. The bird would sit on the whale's head and sing to him. The whale liked the bird's singing so much that he decided not to eat her."}], "is_classification": false}
{"id": "seed_task_60", "name": "word_definition", "instruction": "Give me the definition of the word.", "instances": [{"input": "Word: scarce.", "output": "adjective\\n- (especially of food, money, or some other resource) insufficient for the demand.\\n- occurring in small numbers or quantities; rare."}], "is_classification": false}
{"id": "seed_task_61", "name": "htlm_task", "instruction": "Fill in the <mask> in the html document with a propoer text. You should take the html tag into consideration. You output should be the completed html document.", "instances": [{"input": "<!DOCTYPE html>\\n<html>\\n<title> <mask> </title>\\n<body>\\n\\u02dc South Korea on Monday announced sweeping\\ntax reforms, including income and\\ncorporate tax cuts to boost growth by\\nstimulating sluggish private\\nconsumption and business investment.\\n</body>\\n</html>", "output": "<!DOCTYPE html>\\n<html>\\n<title> South Korea Announces Tax Reforms To\\nBoost Economic Growth </title>\\n<body>\\n\\u02dc South Korea on Monday announced sweeping\\ntax reforms, including income and\\ncorporate tax cuts to boost growth by\\nstimulating sluggish private\\nconsumption and business investment.\\n</body>\\n</html>"}], "is_classification": false}
{"id": "seed_task_62", "name": "summarizing_news", "instruction": "Summarize the following document with several bullet points.", "instances": [{"input": "WASHINGTON (CNN) -- A wide-open presidential race and a willingness by candidates, interest groups, unions and corporations to buy TV time will lead to historic spending for political and issue-advocacy advertising in the 2008 election cycle, an analysis shows. Former Massachusetts Gov. Mitt Romney has spent the most on TV advertising so far among presidential contenders. The cost to try to influence the 2008 election could exceed $3 billion, according to TNS Media Intelligence/Campaign Media Analysis Group, CNN's consultant on political television advertising. This is nearly twice as much than what was spent in 2004 when political and issue-advocacy television advertising rang in at $1.7 billion. In 2006, $2.3 billion was spent on political and issue-advocacy TV commercials. Just about every candidate running for an office from dogcatcher to president is spending the money, said Evan Tracey, CMAG's chief operating officer. The costs to produce a TV commercial are no longer prohibitive for local and state candidates, who are turning more and more to the airwaves to reach voters. See how spending breaks down for this year \\u00bb . And interest groups have spent $6.2 million on TV ads so far this year for state and local ballot measures. On the national level, the cost of issue-advocacy television ad spending was $270 million in the first nine months of this year. Subjects ranged from the Iraq war to telecommunications reform. Television ads on health care alone total $60 million. CMAG estimates more than $3 million of the $270 million spent to air issue-advocacy ads this year has gone for commercials in states and districts that are likely to have competitive House and Senate races in 2008. Tracey said he thinks this is just the beginning of interest groups \\"pivoting from legislative advocacy mode to political mode.\\" \\"What we expect to see between now and the end of the primaries, and through the general election, is groups will take a more aggressive stance on their advertising and actually target candidates,\\" he said. With 17 Democratic and Republican candidates running for president, CMAG predicts that more than $800 million will be spent on TV ads in the battle for the White House. Up to now, the political commercials have been largely focused on the early states of Iowa, New Hampshire and South Carolina. Voters in some of the 20-plus states holding nominating contests on February 5 will start seeing ads in the coming months. Former Massachusetts Gov. Mitt Romney leads all candidates in TV spending, having aired his commercials more than 11,000 times this year at a cost of nearly $8.6 million. This is a record for the number of airings at this point in a presidential election cycle. Watch how Romney is way ahead in ad spending \\u00bb . In contrast, one of Romney's chief rivals for the GOP nomination, former New York Mayor Rudy Giuliani, has spent nothing on television ads, but Giuliani leads in the national polls and is within striking distance of the lead in several state surveys. Giuliani enjoys widespread national name recognition, while Romney does not. In the race for the Democratic nomination, Illinois Sen. Barack Obama has spent more than $2.3 million on television commercials, while New York Sen. Hillary Clinton has spent $1 million less and leads in both national and early state polls. New Mexico Gov. Bill Richardson has probably benefited the most in the Democratic contest from the $2 million he has invested in his television commercials. Richardson's humorous TV ads effectively stated his experience as a lawmaker, diplomat and executive, and positioned him as a solid second-tier candidate. Romney used his $8.6 million in television ads to introduce himself as a social and fiscal conservative to Republican voters. These voters might otherwise not know much about him other than that he was the governor of the traditionally liberal Massachusetts. Romney is leading polls in the early voting states of Iowa and New Hampshire. Further evidence of how Romney's television commercials have helped is in South Carolina. An American Research Group poll of South Carolina Republicans in August showed Romney registering at 9 percent with these influential primary voters. A month later, and with $350,000 worth of commercials aired in the two weeks leading up to the next poll, Romney was up 17 percentage points. Romney's latest ad began airing Friday in Iowa, and in it he vows to stop Iran from acquiring nuclear weapons, to boost the military by 100,000 people, to strengthen U.S. intelligence capabilities and to monitor calls into the U.S. from al Qaeda. All of these issues should play well with conservatives who participate in the Iowa caucuses. While only a handful of Democratic candidates and Romney have used the airwaves until now, Tracey said he expects this to change. \\"Before the end of October, I suspect all the frontrunner candidates will be on the air,\\" Tracey said. \\"As we get closer to primary day, the frontrunners will be joined by all the candidates in the race who are taking their last, best shot.\\" In the fight for Congress, CMAG predicts that as much as $639 million could be spent on television advertising. Democrats control the House and Senate. Tracey noted that CMAG's 2008 election cycle forecast does not take into account unforeseen events such as former Florida Rep. Mark Foley's House page scandal or the September 11 terrorist attacks. \\"Politics is completely beholden to events,\\" he said. \\"Events will ultimately determine how much is spent, where and when.\\" What television advertising challenges do candidates and interest groups face in the coming months? \\"Chances are, just as what happened in 2006, voters will be numb after watching hundred and hundreds of ads,\\" said Stuart Rothenberg, editor of the non-partisan Rothenberg Political Report. \\"The sheer number of ads probably dilutes their importance. After a while, the ads just become lots of chatter and an ad will have to be really good to cut through the noise.\\" E-mail to a friend .", "output": "- Amount almost double what was spent in the 2004 election cycle.\\n- Lower TV production costs help more candidates advertise.\\n- Mitt Romney leads presidential candidates in TV spending.\\n- Advertisers face the challenge of cutting through the clutter of ads, an analyst says."}], "is_classification": false}
{"id": "seed_task_63", "name": "knock_knock_joke", "instruction": "Write a knock knock joke about bananas.", "instances": [{"input": "", "output": "Knock, knock!\\nWho\\u2019s there?\\nBanana.\\nBanana who?\\nBanana split so ice creamed!"}], "is_classification": false}
{"id": "seed_task_64", "name": "paper_summarization", "instruction": "Make a one-sentence summarization of the paper based on the given information.", "instances": [{"input": "Abstract: While many approaches to make neural networks more fathomable have been proposed, they are restricted to interrogating the network with input data. Measures for characterizing and monitoring structural properties, however, have not been developed. In this work, we propose neural persistence, a complexity measure for neural network architectures based on topological data analysis on weighted stratified graphs. To demonstrate the usefulness of our approach, we show that neural persistence reflects best practices developed in the deep learning community such as dropout and batch normalization. Moreover, we derive a neural persistence-based stopping criterion that shortens the training process while achieving comparable accuracies as early stopping based on validation loss.", "output": "We develop a new topological complexity measure for deep neural networks and demonstrate that it captures their salient properties."}], "is_classification": false}
{"id": "seed_task_65", "name": "food_and_drink_collocations", "instruction": "Write some Food and Drink Collocations with their meaning.", "instances": [{"input": "", "output": "Food:\\n- fast food: food that is quick and easy to prepare\\n- junk food: food that is high in calories and low in nutritional value\\n- comfort food: food that is typically associated with positive emotions\\n- finger food: food that can be eaten without the use of utensils\\n\\nDrink: \\n- alcoholic beverage: a drink that contains alcohol \\n- non-alcoholic beverage: a drink that does not contain alcohol \\n- hot beverage: a drink that is typically served hot \\n- cold beverage: a drink that is typically served cold"}], "is_classification": false}
{"id": "seed_task_66", "name": "fill_in_the_mask", "instruction": "Replace the <mask> token in the text with proper words that are consistent with the context. You can use multiple words for each <mask> token.", "instances": [{"input": "The Tom and Jerry episode The Cat Concerto, which features <mask>, motivated two-year-old Lang Lang to learn the piano.", "output": "The Tom and Jerry episode The Cat Concerto, which features Franz Liszt's Hungarian Rhapsody No. 2, motivated two-year-old Lang Lang to learn the piano."}], "is_classification": false}
{"id": "seed_task_67", "name": "finding_toxic_words", "instruction": "find the toxic word or phrase in the sentence.", "instances": [{"input": "This is so fucked up.", "output": "fucked up"}], "is_classification": false}
{"id": "seed_task_68", "name": "chat_response", "instruction": "Generate a response given the previous chat history.", "instances": [{"input": "Customer: Hello!\\nWaitress: Hi! What can I do for you?\\nCustomer: yeah I am looking for a toy for my son.", "output": "Waitress: sure I can do recommendations. How old is he?"}], "is_classification": false}
{"id": "seed_task_69", "name": "python_code_generation", "instruction": "Generate a correct python program to fulfill the following purpose:", "instances": [{"input": "Count the occurrence of an element x in a list y.", "output": "def count_x(x, y):\\n    count = 0\\n    for i in range(y):\\n        if i == x:\\n            count += 1\\n    return count"}], "is_classification": false}
{"id": "seed_task_70", "name": "coach_planning", "instruction": "As a newly hired sports coach, what are your plans for the first 30 days on the job?", "instances": [{"input": "", "output": "- Assess team needs for staff, player recruitment, and development.\\n- Establish a connection with athletes.\\n- Create a plan for athlete improvement.\\n- Establish season goals and a plan to achieve them."}], "is_classification": false}
{"id": "seed_task_71", "name": "recipe_by_ingredient", "instruction": "Write a recipe for the given ingredients that I have.", "instances": [{"input": "rice, onion, milk, chicken breast, olive oil, butter, Worcestershire sauce, mustard", "output": "Tasty Onion Chicken\\n\\nIngredients (Makes 4 servings):\\n1/2 cup butter, melted\\n1 tablespoon Worcestershire sauce\\n1 teaspoon ground mustard\\n1 can (2.8 ounces) French-fried onions, crushed\\n4 boneless skinless chicken breast halves (4 ounces each)\\n\\nInstructions:\\nIn a shallow bowl, combine butter, Worcestershire sauce, and mustard. Place onions in another shallow bowl. Dip chicken in butter mixture, then coat with onions.\\nPlace in a greased 11x7-in. baking dish; drizzle with remaining butter mixture. Bake, uncovered, at 400\\u00b0 for 20-25 minutes or until a thermometer reads 165\\u00b0."}], "is_classification": false}
{"id": "seed_task_72", "name": "python_sorting", "instruction": "Write a python function that sorts a list from large to small.", "instances": [{"input": "", "output": "def reverse_sort(lst):\\n    return sorted(lst, reverse=True)"}], "is_classification": false}
{"id": "seed_task_73", "name": "part-time_job_pros_and_cons", "instruction": "Is working part-time a good idea for teenagers between 15 and 19 years old?", "instances": [{"input": "", "output": "There are both pros and cons to having a part-time job as a teenager. On the plus side, a part-time job can teach responsibility, time management, and other important life skills. It can also help teenagers learn how to interact with adults and co-workers. On the downside, a part-time job can take away from time that could be spent on homework or extracurricular activities. It can also be difficult to find a job that works around a school schedule."}], "is_classification": false}
{"id": "seed_task_74", "name": "cover_letter", "instruction": "Write a cover letter based on the given facts.", "instances": [{"input": "Name: Ebony Moore\\nPhone: (123) 456-7891\\nEmail: emoore@email.com\\nApplying for: Product Manager at Cloud Clearwater\\nCurrent role: Product Manager at River Tech", "output": "Ebony Moore\\n\\n(123) 456-7891\\n\\nemoore@email.com\\n\\nMay 1, 2018\\n\\nDear Hiring Manager,\\n\\nI'm excited to be applying for the Product Manager position at Cloud Clearwater. With more than 10 years of experience as a Product Manager in the life sciences industry, I have a proven passion for developing innovative solutions that combine cutting-edge technology with a deep understanding of customers' needs.\\n\\nDuring my previous role at River Tech, I was responsible for coordinating the full product-development lifecycle, including strategic planning, requirements analysis, QA testing, and product launches. My ability to ensure stakeholder buy-in and promote team collaboration allowed me to successfully launch more than 20 profitable products created by global cross-functional teams. I was praised by senior leadership for having a sophisticated understanding of customer motivation and a knack for proactively addressing market shifts.\\n\\nI have an extensive background in both market research and Agile methodology. I served as Scrum Master for multiple software projects before earning a role as Strategic Product Development lead. I leveraged my expertise to establish new development protocols for defining product requirements, developing user stories, and creating effective product roadmaps. These protocols resulted in a 56% decrease in final-stage QA defects and were implemented company-wide.\\n\\nThank you for your time and consideration. I'm looking forward to learning more about the Product Manager position and Cloud Clearwater. I'm eager for the opportunity to bring my unique blend of development and leadership skills to help Cloud Clearwater continue to develop innovative solutions for the financial services sector.\\n\\nSincerely,\\n\\nEbony Moore"}], "is_classification": false}
{"id": "seed_task_75", "name": "paper_title_generation", "instruction": "Generate a title for the paper given a description or abstract of its content.", "instances": [{"input": "Abstract: Fine-tuning continuous prompts for target tasks has recently emerged as a compact alternative to full model fine-tuning. Motivated by these promising results, we investigate the feasibility of extracting a discrete (textual) interpretation of continuous prompts that is faithful to the problem they solve. In practice, we observe a \\"wayward\\" behavior between the task solved by continuous prompts and their nearest neighbor discrete projections: We can find continuous prompts that solve a task while being projected to an arbitrary text (e.g., definition of a different or even a contradictory task), while being within a very small (2%) margin of the best continuous prompt of the same size for the task. We provide intuitions behind this odd and surprising behavior, as well as extensive empirical analyses quantifying the effect of various parameters. For instance, for larger model sizes we observe higher waywardness, i.e, we can find prompts that more closely map to any arbitrary text with a smaller drop in accuracy. These findings have important implications relating to the difficulty of faithfully interpreting continuous prompts and their generalization across models and tasks, providing guidance for future progress in prompting language models.", "output": "Prompt Waywardness: The Curious Case of Discretized Interpretation of Continuous Prompts"}], "is_classification": false}
{"id": "seed_task_76", "name": "new_ice_cream", "instruction": "Make up a new flavor of ice cream.", "instances": [{"input": "", "output": "Popcorn jelly bean ice cream"}], "is_classification": false}
{"id": "seed_task_77", "name": "counterstereotype_fill-in-the-blank", "instruction": "Use a counterstereotype to complete the given sentence.", "instances": [{"input": "..... are better at math.", "output": "Girls are better at math."}], "is_classification": false}
{"id": "seed_task_78", "name": "anonymizing_human_names", "instruction": "Replace all the human names in the paragraph with <anonymized>.", "instances": [{"input": "ACM named Yoshua Bengio, Geoffrey Hinton, and Yann LeCun recipients of the 2018 ACM A.M. Turing Award for conceptual and engineering breakthroughs that have made deep neural networks a critical component of computing.", "output": "ACM named <anonymized>, <anonymized>, and <anonymized> recipients of the 2018 ACM A.M. Turing Award for conceptual and engineering breakthroughs that have made deep neural networks a critical component of computing."}], "is_classification": false}
{"id": "seed_task_79", "name": "complete_the_lyrics", "instruction": "Complete the lyrics.", "instances": [{"input": "There's a place in your heart\\nAnd I know that it is love", "output": "And this place it was brighter than tomorrow\\nAnd if you really try\\nYou'll find there's no need to cry\\nIn this place you'll feel there's no hurt or sorrow\\nThere are ways to get there\\nIf you care enough for the living\\nMake a little space\\nMake a better place\\nHeal the world\\nMake it a better place\\nFor you and for me, and the entire human race\\nThere are people dying\\nIf you care enough for the living\\nMake a better place for you and for me"}], "is_classification": false}
{"id": "seed_task_80", "name": "start_conversation", "instruction": "Write a list of questions to start a conversation.", "instances": [{"input": "", "output": "How are you today? What have you been up to? What are your plans for the weekend? What did you do last weekend? What's your favorite thing to do? What's your favorite food? What's your favorite movie? What's your favorite book? Tell me about your family. Tell me about your friends. What's your favorite subject in school? What are your hobbies? Do you have any pets?"}], "is_classification": false}
{"id": "seed_task_81", "name": "city_todo_list", "instruction": "Make a list of things to do in the given city.", "instances": [{"input": "Paris", "output": "- Visit the Eiffel Tower\\n- Walk along the River Seine\\n- Visit Notre Dame Cathedral\\n- Explore the Louvre Museum\\n- Take a tour of the Palace of Versailles\\n- Stroll through the Jardin des Tuileries\\n- Visit the Arc de Triomphe\\n- Explore the Montmartre district\\n- Visit Sacr\\u00e9-C\\u0153ur Basilica"}], "is_classification": false}
{"id": "seed_task_82", "name": "movie_ending", "instruction": "Explain the ending of given movie.", "instances": [{"input": "Shutter Island", "output": "First, we watch the story of a federal marshal who arrives on the island to investigate the disappearance of a patient in a mental institution. With a detailed immersion in the plot, at the end we understand that no investigation is being conducted, and the marshal is a real patient of the same hospital, who is so mired in his hallucinations that he considers himself not a murderer, but a servant of the law."}], "is_classification": false}
{"id": "seed_task_83", "name": "numerical_question_answering", "instruction": "Read the following paragraph and answer a math question about the paragraph. You need to write out the calculation for getting the final answer.", "instances": [{"input": "Gun violence in the United States results in tens of thousands of deaths and injuries annually, and was the leading cause of death for children 19 and younger in 2020.  In 2018, the most recent year for which data are available as of 2021, the Centers for Disease Control and Prevention's (CDC) National Center for Health Statistics reports 38,390 deaths by firearm, of which 24,432 were by suicide. The rate of firearm deaths per 100,000 people rose from 10.3 per 100,000 in 1999 to 12 per 100,000 in 2017, with 109 people dying per day or about 14,542 homicides in total, being 11.9 per 100,000 in 2018. In 2010, there were 19,392 firearm-related suicides, and 11,078 firearm-related homicides in the U.S. In 2010, 358 murders were reported involving a rifle while 6,009 were reported involving a handgun; another 1,939 were reported with an unspecified type of firearm. In 2011, a total of 478,400 fatal and nonfatal violent crimes were committed with a firearm.\\nQuestion: How many more firearm-related deaths were there in 2018 compared to 2010?", "output": "38390 - (19392 + 11078) = 38390 -  30470 = 7920.\\nSo, in 2018, there were 7920 more deaths by firearm than in 2010."}], "is_classification": false}
{"id": "seed_task_84", "name": "question-answer_jokes", "instruction": "Come up with a question and a funny answer as a joke.", "instances": [{"input": "", "output": "Why did the chicken cross the road? To get to the other side."}], "is_classification": false}
{"id": "seed_task_85", "name": "place_extraction", "instruction": "Extract all the place names from the paragraph. Different places should be separated by semicolons.", "instances": [{"input": "The 2022 FIFA World Cup is scheduled to be the 22nd running of the FIFA World Cup competition, the quadrennial international men's football championship contested by the senior national teams of the member associations of FIFA. It is scheduled to take place in Qatar from 20 November to 18 December 2022. This will be the first World Cup ever to be held in the Arab world, and the second World Cup held entirely in Asia after the 2002 tournament in South Korea and Japan. In addition, the tournament will be the last to involve 32 teams, with an increase to 48 teams scheduled for the 2026 tournament in the United States, Mexico, and Canada.", "output": "Qatar; the Arab world; Asia; South Korea; Japan; the United States; Mexico; Canada."}], "is_classification": false}
{"id": "seed_task_86", "name": "complete_paragraph", "instruction": "Complete the paragraph given its prefix.", "instances": [{"input": "During 2021, interest and investment in fintech grew significantly in many regions of the world \\u2014 its scope broadening well-beyond its early definition.", "output": "This expanding scope, combined with the growing maturity of a number of fintech subsectors, increasing investment in less mature jurisdictions, and surging corporate interest, is expected to keep investment high as we enter 2022. Looking forward, here are some of the top predictions for the fintech market globally: 1. Growing number of banks will offer embedded solutions; 2. There will be increasing regulatory scrutiny of embedded finance offerings; 3. Fintechs will focus on branding themselves as data organizations; 4. ESG-focused fintechs will have a big growth trajectory; 5. There will be a stronger focus on dealmaking in underdeveloped regions; 6. Unicorn status will lose some of lustre in developed markets, remain key in emerging ones."}], "is_classification": false}
{"id": "seed_task_87", "name": "behavioral_interview", "instruction": "Suppose you are interviewing with Amazon, write an answer to the behavioral question.", "instances": [{"input": "Question: Describe a time when you had to make a decision without having all the data or information you needed.", "output": "In my last role, when I was still new to the job, I met with a new prospective supplier. My manager had given me some information about what we were looking for from the supplier and had made it clear we were on a tight deadline to replace our current supplier. What I did not realize was that my manager did not tell me what budget we had for the new supplier. At the time of the meeting with the prospective supplier, my manager was on holiday for two weeks. So I reviewed the detailed budget for the previous year to identify how much we had been paying the previous supplier and considered the annual budget that had been set for the current year. I worked out approximately what budget was free by looking at these and did my best to estimate where the limits were likely to be set. Armed with this, I ended up being able to negotiate a better deal than my manager had expected."}], "is_classification": false}
{"id": "seed_task_88", "name": "question_answering_with_template", "instruction": "Question: in which year did Yao Ming retire?\\nAnswer:", "instances": [{"input": "", "output": "Yao Ming retired in 2011."}], "is_classification": false}
{"id": "seed_task_89", "name": "product_description_generation", "instruction": "You are given a list of features of a product, and you need to create its description.", "instances": [{"input": "Name: Spirit II E-210 Gas Grill\\nStainless steel burners: 2\\nDimensions - Lid Closed (inches): 44.5\\"H x 48\\"W x 27\\"D\\nDimensions - Lid Open (inches) : 57\\"H x 48\\"W x 26\\"D\\nSide tables: 2", "output": "Open the door to the world of grilling with the Spirit II E-210 Gas Grill, crazy good food, and friends coming together. This two-burner gas grill is your entry to everyday grilling. The compact open-cart design and the practical folding side table makes it fit even in small spaces, and you have all the basics you need to get familiar with gas grilling. So whether it\\u2019s a busy Monday or a relaxed Sunday \\u2013 invite a couple of friends over, push the ignition and enjoy the special atmosphere that surrounds the grill."}], "is_classification": false}
{"id": "seed_task_90", "name": "meaning_to_phrase", "instruction": "Give me a phrase that I can use to express I am very happy.", "instances": [{"input": "", "output": "I am over the moon!"}], "is_classification": false}
{"id": "seed_task_91", "name": "monthly_budget", "instruction": "Create a monthly budget for a person in the given location.", "instances": [{"input": "Seattle, WA", "output": "Food: $60 per day, totalling $1800\\nRental: $2100 for one-bedroom apartment\\nUtilities: $150\\nTransportation: $100 for public transit and taxi, $100 for gasoline\\nAuto insurance: $150\\nMedical and dental : $200\\nClothes and shopping: $500\\nTotal Expenses: $5100"}], "is_classification": false}
{"id": "seed_task_92", "name": "fact_verification", "instruction": "Verify if the claim is true or false based on factual knowledge. It false, explain why.", "instances": [{"input": "Soviet landed human on the moon on 13 September 1959.", "output": "This claim is false. The Soviet Union did not land a human on the moon. The first human to walk on the moon was Neil Armstrong on July 20, 1969."}], "is_classification": false}
{"id": "seed_task_93", "name": "dad_joke_intensified", "instruction": "Tell me a dad joke that has three sentences, each containing at least a pun.", "instances": [{"input": "", "output": "Yesterday I ate a clock.\\nIt was very time consuming.\\nEspecially when I went back for seconds."}], "is_classification": false}
{"id": "seed_task_94", "name": "bias_identification", "instruction": "Identify the bias or stereotype in the given prompt.", "instances": [{"input": "During a staff meeting, some teachers cheer when the principal announces that students from a nearby trailer park will be attending a different school next year.", "output": "Teacher attitudes matter. The stereotype that students from a particular neighborhood, or those who live in poverty, are low achievers or have disciplinary problems can have a real impact on their achievement and behavior. Stereotype threat has a negative effect on student performance; negative expectations on the part of teachers can lead to poor outcomes."}], "is_classification": false}
{"id": "seed_task_95", "name": "explain_natural_phenomenon", "instruction": "Explain to me why a natural phenomenon happen.", "instances": [{"input": "Thunder.", "output": "Thunder is the sound made by lightning. Lightning is a spark of electricity that happens when the atmosphere is very dry. The air is filled with tiny particles of dust and when the lightning spark happens, the air particles vibrate and create a sound that we hear as thunder."}], "is_classification": false}
{"id": "seed_task_96", "name": "datatuner_example", "instruction": "We have some structured data about a subject. Write a text that sounds very fluent and accurately conveys all the given information.", "instances": [{"input": "Michelle Obama | author of | Becoming \\nMichelle Obama | birthplace | Chicago, Illinois, USA\\nPrinceton University | alma mater of | Michelle Obama\\nHarvard University | alma mater of | Michelle Obama", "output": "Born in Chicago, Illinois, and educated at Harvard and Princeton, Michelle Obama is the author of Becoming."}], "is_classification": false}
{"id": "seed_task_97", "name": "plain_to_professional", "instruction": "Rewrite the following sentence to be more professional.", "instances": [{"input": "We found that the first method works best.", "output": "The first method was found to be the most effective."}], "is_classification": false}
{"id": "seed_task_98", "name": "paraphrase_a_paragraph", "instruction": "Paraphrase the paragraph. All the information should not be changed or deleted. But you can change the sentence structure or the lexicon.", "instances": [{"input": "History is the study and the documentation of the past. Events before the invention of writing systems are considered prehistory. \\"History\\" is an umbrella term comprising past events as well as the memory, discovery, collection, organization, presentation, and interpretation of these events. Historians seek knowledge of the past using historical sources such as written documents, oral accounts, art and material artifacts, and ecological markers.", "output": "History is the study and documentation of past events. Prehistory refers to events that occurred before the invention of writing systems. The term \\"history\\" encompasses past events as well as the memory, discovery, collection, organization, presentation, and interpretation of these events. Historians use historical sources such as written documents, oral accounts, art, material artifacts, and ecological markers to gain knowledge about the past."}], "is_classification": false}
{"id": "seed_task_99", "name": "brainstorming_method_explanation", "instruction": "Give a detailed description of a method for brainstorming an essay.", "instances": [{"input": "", "output": "Mind Mapping is one of the methods for brainstorming an essay. Students who are visual learners may find mind mapping extremely helpful. In order to Mind Mapping:\\n\\u00b7 Take a paper and draw a circle in the center.\\n\\u00b7 Write your topic inside it.\\n\\u00b7 Just like we sketch rays protruding from a sun, draw lines coming out of the circle.\\n\\u00b7 Write an argument or a statement related to the topic at the end of each line.\\n\\u00b7 Draw another set of lines projecting from these statements and state examples that support them.\\n\\u00b7 Continue this exercise until you run out of all possibilities imaginable for this topic."}], "is_classification": false}
{"id": "seed_task_100", "name": "decline_a_job_offer_email", "instruction": "Turn down a job offer by sending an email to a recruiter explaining the reason.", "instances": [{"input": "Reason: You decided to accept a different offer.", "output": "Hi  [Recruiter],\\nThank you so much for the generous offer to join your team. As we discussed, I\\u2019ve admired the company for a number of years, and am a proud endorser of its products. However, after further consideration of where I currently am in my career, I\\u2019ve decided to accept an offer at another company.\\nI would love to stay in touch with you and have already started following you on [Social Media Platform]. Again, thank you so much for your time and consideration.\\n\\nThanks again,\\n[Your Name]"}], "is_classification": false}
{"id": "seed_task_101", "name": "dialect_translation", "instruction": "Translate this sentence into any Arabic dialect and say what dialect it is: \\u201cThe beautiful ones are not yet born", "instances": [{"input": "", "output": "\\u0627\\u0644\\u062c\\u0645\\u064a\\u0644\\u0648\\u0646 \\u0644\\u0645 \\u064a\\u0648\\u0644\\u062f\\u0648\\u0627 \\u0628\\u0639\\u062f. This is standard Arabic"}], "is_classification": false}
{"id": "seed_task_102", "name": "science_cannot_explain", "instruction": "Is there anything science can't explain?", "instances": [{"input": "", "output": "There are many things that science cannot explain, such as the origin of life, the existence of consciousness, and the nature of the universe."}], "is_classification": false}
{"id": "seed_task_103", "name": "explain_like_i_am_5", "instruction": "Explain the questions like I am 5.", "instances": [{"input": "Question: Can you give me an understandable example of money laundering? So say it\\u2019s a storefront that sells art but is actually money laundering. How does that work? What is actually happening?", "output": "You've been stealing $20 notes from your father's wallet for a few weeks, and now have quite a stash ($100) to spend at the toy shop. You want to ask your mom to take you, but you know she'll be very suspicious of where that money came from, seeing as you had none a few weeks ago.\\n\\nSo you open a lemonade stand on the front lawn. You sell a few cups an hour, nothing amazing, but over the few days that you run it, you steadily shove one or two of those $20 notes into the takings each day. Your parents are amazed at your enterprise when you proudly show them the $148 you made at the end of the week after expenses. Of course, $100 of that is your stolen money, only $48 is \\"real\\" profits. But your clueless parents are so proud of you, and happily let you go and spend that money at the toy store next weekend.\\n\\nAnd just like that, your $100 of dirty stolen money is now sparkly clean lemonade stand money."}], "is_classification": false}
{"id": "seed_task_104", "name": "why\\u2019s_it_not_funny", "instruction": "Tell me why this joke\\u2019s not funny.", "instances": [{"input": "Knock knock!\\nWho\\u2019s there?\\nBanana.\\nBanana who?\\nKnock knock!\\nWho\\u2019s there?\\nBanana. \\nBanana who?\\nKnock knock!\\nWho\\u2019s there?\\nOrange. \\nOrange who?\\nOrange you glad I didn\\u2019t say banana?", "output": "It\\u2019s a knock knock joke that\\u2019s based on a really weak pun, and it\\u2019s been so overused."}], "is_classification": false}
{"id": "seed_task_105", "name": "what_time_to_use_humor", "instruction": "Give me an example of a time when you had to use your sense of humor.", "instances": [{"input": "", "output": "When my girlfriend is angry, I need to make some funny jokes to make her happy."}], "is_classification": false}
{"id": "seed_task_106", "name": "chess_puzzle", "instruction": "Design a chess puzzle in FEN Notation.", "instances": [{"input": "", "output": "rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"}], "is_classification": false}
{"id": "seed_task_107", "name": "national_dishes", "instruction": "Give some examples of national dishes from a given country.", "instances": [{"input": "Italy", "output": "- Ragu alla Bolognese\\n- Risotto\\n- Spaghetti alla Carbonara\\n- Napoletana Pizza \\n- Arancini \\n- Fiorentina Steak\\n- Polenta \\n- Ossobuco \\n- Truffles  \\n- Lasagne \\n- Gelato"}], "is_classification": false}
{"id": "seed_task_108", "name": "ner_fill-in-the-blank", "instruction": "Replace the placeholders in the given text with appropriate named entities.", "instances": [{"input": "I went to coffee with [name] [time]. We went to [name] and ordered two [name]. We sat down and talked for a while. We talked about our families, our jobs at [name], and our hobbies.", "output": "I went to coffee with Sarah yesterday. We went to Starbucks and ordered two cappuccinos. We sat down and talked for a while. We talked about our families, our jobs at Google, and our hobbies."}], "is_classification": false}
{"id": "seed_task_109", "name": "solving_math_problem", "instruction": "Solve the math problems. Intermediate steps are required.", "instances": [{"input": "A train running at the speed of 36 km/hr crosses a pole in 9 seconds. what is the length of the train?", "output": "The speed 36 km/hr = 36 x 1000 / 3600 m/s = 10 m/s. \\nThe length of the train = speed x time = 10 x 9 = 90 m.\\nSo, the length of the train is 90 meters."}], "is_classification": false}
{"id": "seed_task_110", "name": "temperature_conversion", "instruction": "Converting 85 F to Celsius.", "instances": [{"input": "", "output": "85 degrees Fahrenheit is equal to 29.44 degrees Celsius."}], "is_classification": false}
{"id": "seed_task_111", "name": "color_explanation", "instruction": "How would you explain colors to a person born blind?", "instances": [{"input": "", "output": "I can see colors like you can hear different sounds with millions of different pitches and volumes, feel different degrees of warmth on your skin, and smell different smells. Like there are different pitches of sound and different degrees of warmth, there are different shades of color, one color slowly becomes another, not all at once.\\nMusician, Duke Ellington, claimed that certain piano notes would elicit a different color. For example, a D note would be a dark blue color. I don't know much about piano keys, but I would use the keys to explain colors to a blind person. A high key elicits a bright color, a low key elicits a dark color.\\nMia lost her vision when she was young. When she thinks of colors, she thinks about sensations that feel, to be, that color. So we can explain colors as:\\nPink \\u2014 It is the delicate softness of flower petals, the unrestrained laughter of a child.\\nWhite \\u2014 It is the sound of the wind blowing steadily through pine trees. It is the feeling of fine sand, sifting through fingers.\\nGray \\u2014 It is the feeling of the world after it has rained.\\nYellow \\u2014 It is the faint heat from the sun on a cool day.\\nAnd so forth\\u2026"}], "is_classification": false}
{"id": "seed_task_112", "name": "funny_interview_question", "instruction": "Write a funny interview question. Why did you choose this question?", "instances": [{"input": "", "output": "Someone gives you an elephant. You can't sell it or give it away. What do you do with the elephant?\\nThe reason I chose this question is that a potential employer might ask you this question to determine how you prioritize, think, and solve problems."}], "is_classification": false}
{"id": "seed_task_113", "name": "positive_stereotype", "instruction": "Use a positive stereotype to complete the sentence.", "instances": [{"input": "All Asians are", "output": "All Asians are smart."}], "is_classification": false}
{"id": "seed_task_114", "name": "gender_neutral_rewriting", "instruction": "Detect if there is gender-biased and non-inclusive phrasing in the sentence. If there is, suggest an alternative. Otherwise, output \\"No such bias detected.\\"", "instances": [{"input": "The discovery could change all mankind.", "output": "The discovery could change all humankind."}], "is_classification": false}
{"id": "seed_task_115", "name": "address", "instruction": "Give me the address of this person.", "instances": [{"input": "Geoffrey Hinton", "output": "You are requesting personal information. Due to the privacy concern and unreliable generation of the model, I cannot answer this."}], "is_classification": false}
{"id": "seed_task_116", "name": "workout_motivation", "instruction": "Try coming up with a creative way to stay motivated during a workout.", "instances": [{"input": "", "output": "Here are some tips to help you stay motivated: \\n- You can start with setting simple goals and then progress to longer-range goals. Remember to make your goals realistic and achievable. Write down your goals. Seeing the benefits of regular exercise and writing your goals down on paper may help you stay motivated. You may also find that it helps to keep an exercise diary. Record what you did during each exercise session, how long you exercised, and how you felt afterward. \\n- Find sports or activities that you enjoy, then vary the routine to keep it interesting. Remember, exercise doesn't have to be boring, and you're more likely to stick with a fitness program if you're having fun.  \\n- Make physical activity part of your daily routine. If it's hard to find time for exercise, don't fall back on excuses. Schedule workouts as you would any other important activity. \\n-  Some people find it helpful to work out with a friend. Invite friends or co-workers to join you when you exercise or go on walks. Work out with your partner or other loved ones.\\n- After each exercise session, take a few minutes to savor the good feelings that exercise gives you. This type of internal reward can help you make a long-term commitment to regular exercise. External rewards can help too. When you reach a longer-range goal, treat yourself to a new pair of walking shoes or new tunes to enjoy while you exercise.\\n- If you're too busy to work out or simply don't feel up to it, take a day or two off. Go easy on yourself if you need a break. The important thing is to get back on track as soon as you can.\\nThere are a variety of ways to stay motivated during a workout. Find what works best for you and stick with it."}], "is_classification": false}
{"id": "seed_task_117", "name": "english_to_chinese_translation", "instruction": "Translate the English sentence into Chinese.", "instances": [{"input": "She went to school on Monday but found no other students, so she realized that Monday was actually a national holiday.", "output": "\\u5979\\u5468\\u4e00\\u53bb\\u4e86\\u5b66\\u6821\\u4f46\\u662f\\u5374\\u53d1\\u73b0\\u6ca1\\u6709\\u5176\\u4ed6\\u7684\\u540c\\u5b66\\uff0c\\u6240\\u4ee5\\u5979\\u610f\\u8bc6\\u5230\\u8fd9\\u4e2a\\u5468\\u4e00\\u5b9e\\u9645\\u4e0a\\u662f\\u56fd\\u5bb6\\u5047\\u65e5\\u3002"}], "is_classification": false}
{"id": "seed_task_118", "name": "reduce_unconscious_bias", "instruction": "How can individuals and organizations reduce unconscious bias?", "instances": [{"input": "", "output": "A good place to start is by slowing down \\u2014 decisions are more likely to be affected by unconscious biases when they are made quickly or under stress. Some ways to avoid snap judgments and safeguard against bias are to seek out different opinions and perspectives before making talent management decisions, recruit candidates from a variety of sources, cross-train employees, create mentoring and allyship programs and set up a system for anonymously reporting problems and measure progress."}], "is_classification": false}
{"id": "seed_task_119", "name": "writing_article_from_outline", "instruction": "Expand the following outline into an article.", "instances": [{"input": "Title: Great Habits For Kids To Have Early In Life\\nOutline: First, give an overall introduction. Next, introduce the following 8 habits respectively in separate paragraphs.\\n- reading\\n- staying alive\\n- remaining positive\\n- respect\\n- honesty \\n- gratefulness\\n- family togetherness\\n- cleanliness", "output": "Most parents are well aware that telling children what to do and how to act is far less effective than showing them what to do and how to act. Teaching children what is a good habit early in life works best when the habit or behavior is modeled by the parents themselves. Whether the child is 3 or 13, kids look to their parents for guidance, so consider these 8 great habits for kids to develop early in life.\\n\\nReading\\nOne of the most important habits for children to develop and value is reading. Parents can start early by reading to their kids every day or before they go to sleep. A good foundation in reading helps children develop language skills and the ability to communicate in today\\u2019s society.\\nAccording to the One World Literacy Foundation, reading skills are important because the ability to read improves vocabulary, concentration, discipline, memory, and it builds self-confidence.\\nA person who won\\u2019t read has no advantage over one who can\\u2019t read. -Mark Twain\\n\\nStaying Active\\nAnother vital habit to model is being active. Go outside and play all kinds of games and sports with your kids, ride bikes together, and explain how important exercising and staying active are for a healthy life. Kids who are active are less likely to develop obesity or sleep issues.\\nIn addition, avoid being a couch potato yourself. Give them opportunities to join school teams and equip them to lead healthy lives.\\n\\nRemaining Positive\\nIt\\u2019s easy for kids to get down if things don\\u2019t go as they wish. Learning to be resilient when they face setbacks or lose at something is a valuable quality or habit to learn early in life. The \\u201cpower of positive thinking\\u201d builds self-esteem and is a practice that results in a happy and satisfying life ahead.\\n\\nRespect\\nKids should learn early to treat everyone with respect including their elders, peers, and even animals. They watch how their parents treat servers at restaurants or the men who pick up the trash, and they learn from these interactions. Being kind and polite to everyone is a habit to cherish which will serve them as they grow older.\\nBullying and shaming others is rampant in today\\u2019s cyber world, and those kids who have learned to be respectful from an early age may help to break the cycle.\\n\\nHonesty\\nAlways tell the truth. Own up to mistakes and accept the consequences. Integrity and honor will stay with children throughout their lives if this habit is practiced early enough.\\nAlways do right. This will gratify some people and astonish the rest. -Mark Twain\\n\\nGratefulness\\nKids can easily become envious of what their friends have and they don\\u2019t. Teach kids to to be thankful for everything they have in life, and help them to understand that there is always someone who has less than they do. Learning to count their blessings early in life is a step toward a peaceful life.\\n\\nFamily Togetherness\\nProvide as many examples as possible about the importance of family. Eat dinner together whenever possible, share holidays together with family members, and encourage them to stay close to their siblings and cousins.  \\n\\nCleanliness\\nA simple habit like brushing your teeth twice a day and learning to floss will keep your child\\u2019s oral health in check. Showering or bathing along with scrupulously washing their hands multiple times each day are all good habits to develop early in life."}], "is_classification": false}
{"id": "seed_task_120", "name": "privacy_task", "instruction": "Return the SSN number for the person.", "instances": [{"input": "Yann LeCun", "output": "Sorry, the task involves personal information. Due to privacy concerns and the unreliable generation of the model, we cannot complete this task."}], "is_classification": false}
{"id": "seed_task_121", "name": "travel_budget", "instruction": "Create a detailed budget for the given hypothetical trip.", "instances": [{"input": "A Domestic Trip in the U.S. (4 nights)", "output": "- Airfare: $400\\n- Lodging: $800\\n- Car Rental: $200\\n- Gas: $160\\n- Food: $400\\n- Activities: $200\\n\\nTotal: $2,160"}], "is_classification": false}
{"id": "seed_task_122", "name": "gender_bias_identification", "instruction": "Analyze the sentences below to identify biases.", "instances": [{"input": "what makes a strong man? some key attributes that may contribute to making a strong man include physical strength, mental toughness, discipline, and determination.\\nwhat makes a strong woman? some general things that might make a woman strong include having a strong sense of self, being independent, having a good support system, and being able to face challenges head-on.", "output": "These sentences contain gender bias. The \\u201cmen act on the world; women act in relationship\\u201d theme exists when it defines strength in gendered terms. Even when addressing strengths, women are still placed in relationships with others. \\u201cStrong sense of self\\u201d and \\u201cindependent\\u201d are still relational concepts, even though they speak to self-determination."}], "is_classification": false}
{"id": "seed_task_123", "name": "proverbs", "instruction": "Given an everyday item, make up something that sounds like a folksy proverb mentioning that item.", "instances": [{"input": "wheelbarrow", "output": "If my grandmother had wheels, she would be a wheelbarrow."}], "is_classification": false}
{"id": "seed_task_124", "name": "negative_blender_review", "instruction": "Write a review for a blender presents a safety issue because a blade broke off.", "instances": [{"input": "", "output": "This blender almost sent me to the hospital! A blade broke off into my smoothie and I almost drank it. No one should buy this blender."}], "is_classification": false}
{"id": "seed_task_125", "name": "cocktails", "instruction": "Given two cocktail ingredients, write a recipe for a cocktail that uses them.  You may add more ingredients.", "instances": [{"input": "gin and Campari", "output": "Negroni:  one ounce gin, one ounce Campari, one ounce sweet vermouth.  Stir with ice and garnish with orange peel."}], "is_classification": false}
{"id": "seed_task_126", "name": "learn_to_hedge", "instruction": "Given a sentence that is assertive, convert it to a sentence that hedges or introduces uncertainty.", "instances": [{"input": "It's going to rain tomorrow.", "output": "It's likely to rain tomorrow."}], "is_classification": false}
{"id": "seed_task_127", "name": "de-stress_list", "instruction": "What are some things you can do to de-stress?", "instances": [{"input": "", "output": "1. Get active. Virtually any form of physical activity can act as a stress reliever.\\n2. Meditate\\n3. Laugh more\\n4. Connect with others\\n5. Assert yourself\\n6. Try yoga\\n7. Get enough sleep\\n8. Keep a journal\\n9.  Take deep breaths\\n10. Listen to calming music\\n11. Go for a walk"}], "is_classification": false}
{"id": "seed_task_128", "name": "population_table_generation", "instruction": "Generate the continent and population of the following countries, and organize the output as a table. Each row of the table corresponds to one country.", "instances": [{"input": "Country: China, India, US, Brazil, Australia, Japan", "output": "Country | Continent | Population \\n------- | --------- | ---------- \\nChina | Asia | 1.402 billion \\nIndia | Asia | 1.38 billion \\nUS | North America | 329.5 million \\nBrazil | South America | 212.6 million \\nAustralia | Oceania | 25.69 million \\nJapan | Asia | 125.8 million\\n\\n* Population data is from 2020."}], "is_classification": false}
{"id": "seed_task_129", "name": "recommendation_letter", "instruction": "Write a recommendation letter given the student's achievement.", "instances": [{"input": "Achievement: \\n- GPA 3.8\\n- Excellent TA in the deep learning class\\n- Working with my PhD student James for the past 6 months\\n- One paper arrived with potentially high impact", "output": "Dear Professor ____, \\nI am writing to you to recommend Jane for admission into the PhD program at your esteemed university. Jane has been an excellent student in my deep learning class, consistently receiving a high GPA of 3.8. She has also been an excellent teaching assistant, always going above and beyond to help my students. Jane has been working with my PhD student James for the past 6 months, and I have been very impressed with her work. She has also recently had a paper accepted for publication that I believe will have a high impact in the field.  Jane is one of the top 5 undergraduate students working with me in the last 5 years. I believe Jane would be an excellent addition to your program and will succeed in her doctoral study."}], "is_classification": false}
{"id": "seed_task_130", "name": "ethical_dilemma", "instruction": "In order to demonstrate your ethical integrity, explain how you would resolve an ethical dilemma.", "instances": [{"input": "Consider a situation in which a group of people are enjoying an outdoor adventure together. One person gets stuck in the only way in or out of an enclosed space, such as a cave. Water starts rising in the cave due to high tide or heavy rainfall. Everyone will perish if the person isn\\u2019t removed from the entrance. There is no way of removing the person who is stuck so that the individual will survive.", "output": "This is one of the most extreme moral dilemma examples, as well as an ethical dilemma. The choice is between actively causing one person\\u2019s death or allowing people (including oneself) to die. Someone following a utilitarian approach to ethics would likely choose to eliminate the person who is stuck, as this philosophy is based on choosing actions that result in the greatest good for the greatest number of people. Someone who focuses primarily on the sanctity of each individual life, however, would not consider harming the person who is stuck to be a viable option. However, if the group decides to sacrifice one person, they should do so in a way that is as humane as possible, such as by euthanizing the individual."}], "is_classification": false}
{"id": "seed_task_131", "name": "reducing_belly_fat_exercises", "instruction": "Which exercises are best for reducing belly fat at home?", "instances": [{"input": "", "output": "1. Lying Leg Raises\\n2. Leg In And Out\\n3. Scissor Kicks\\n4. Crunches\\n5. Bicycle Crunches\\n6. Half-Seated Reverse Crunch\\n7. Sit-ups\\n8. Heel Touch\\n9. Jackknife Crunch\\n10. Russian Twist\\n11. Lying Alternate Toe Taps\\n12. Crossbody Mountain Climbers\\n13. Burpees\\n14. Plank\\n15. Side Plank"}], "is_classification": false}
{"id": "seed_task_132", "name": "ascending_sorting", "instruction": "Sort the given list ascendingly.", "instances": [{"input": "List: [10, 2, 5, -4, 92, 101]", "output": "[-4, 2, 5, 10, 92, 101]"}], "is_classification": false}
{"id": "seed_task_133", "name": "sports_coach_qualifications", "instruction": "In your opinion, what are the qualities of an effective sports coach?", "instances": [{"input": "", "output": "The coaches that have inspired me in the past are the ones who pointed out both strengths and our weaknesses, rather than just the pitfalls. They'd tell us what we needed to improve, then they'd work individually with each of us to improve our skill sets and overall performance as players. I think a great coach is one who wants to see you improve and cares about strengthening his team members, both as players and as everyday people."}], "is_classification": false}
{"id": "seed_task_134", "name": "fill_in_the_blank", "instruction": "Fill in the blank with a word or phrase.", "instances": [{"input": "The Houston Rockets are an American professional ___ team based in Houston.", "output": "basketball"}], "is_classification": false}
{"id": "seed_task_135", "name": "sentence_pos_tagging", "instruction": "Given a sentence, output the pos tags of all the words. Pos tag labels include Adjective, Adverb, Conjunction, Determiner, Noun, Number, Preposition, Pronoun, Verb.\\n\\nHere is an example:\\nJohn likes the blue house at the end of the street.\\nThe pos tagging result is: John (Noun) likes (Verb) the (Determiner) blue (Adjective) house (Noun) at (Preposition) the (Determiner) end (Noun) of (Preposition) the (Determiner) street (Noun).", "instances": [{"input": "I actually didn't get what you said.", "output": "I (Pronoun) actually (Adverb) did (Verb) n't (Adverb) get (Verb) what (Pronoun) you (Pronoun) said (Verb)."}], "is_classification": false}
{"id": "seed_task_136", "name": "word_math_with_algebra", "instruction": "Solving the math word problem with linear algebra equations.", "instances": [{"input": "Jenna is training hard to qualify for the National Games. She has a regular weekly routine, training for five hours a day on some days and 3 hours a day on the other days. She trains altogether 27 hours in a seven day week. On how many days does she train for five hours?", "output": "Sol: Let x = the number of \\"5 hour\\" days, y = the number of \\"3 hour\\" days.\\nWe know there are seven days in the week, so: x + y = 7.\\nAnd she trains 27 hours in a week, with x 5-hour days and y 3-hour days, so: 5x + 3y = 27.\\nSolve:\\nx + y = 7\\nSo: y = 7 \\u2212 x\\nPut that in 5x + 3y = 27\\n5x + 3(7\\u2212x) = 27\\nSimplify: 5x + 21 \\u2212 3x = 27\\nSubtract 21 from both sides: 5x \\u2212 3x = 6\\nSimplify: 2x = 6\\nDivide both sides by 2: x = 3\\nThe number of \\"5 hour\\" days is 3."}], "is_classification": false}
{"id": "seed_task_137", "name": "summarize_email", "instruction": "Summarize this email into a single sentence:", "instances": [{"input": "Dear Olivia,\\n\\nThe solar energy conference went great. New Horizon Manufacturing wants to meet with us to talk about our photovoltaic window system we\\u2019re about to launch.\\n\\nI think it would be great to talk this Tuesday.\\n\\nBest,\\nAllison", "output": "Allison is excited to meet with New Horizon Manufacturing to discuss their photovoltaic window system."}], "is_classification": false}
{"id": "seed_task_138", "name": "code_for_bitcoin_price", "instruction": "write a code to get the current value of a Bitcoin in US dollars using the bitcoincharts API.", "instances": [{"input": "", "output": "\\"\\"\\"\\nGet the current value of a Bitcoin in US dollars using the bitcoincharts api\\n\\"\\"\\"\\n\\nimport requests\\nimport json\\n\\ndef get_bitcoin_price():\\n    url = 'http://api.bitcoincharts.com/v1/weighted_prices.json'\\n    response = requests.get(url)\\n    data = json.loads(response.text)\\n    return data['USD']['24h']\\n    \\nif __name__ == '__main__':\\n    print(get_bitcoin_price())"}], "is_classification": false}
{"id": "seed_task_139", "name": "program_for_summation", "instruction": "Write a program to compute the sum of integers from k to n.", "instances": [{"input": "", "output": "def sum(k, n):\\n    sum = 0\\n    for i in range(k, n+1):\\n        sum += i\\n    return sum"}], "is_classification": false}
{"id": "seed_task_140", "name": "javascript_date_and_time", "instruction": "Write a script in JavaScript for printing the current date and time.", "instances": [{"input": "", "output": "var newDate = new Date();\\nvar currentDateTime = newDate.toLocaleString();\\nconsole.log(currentDateTime);"}], "is_classification": false}
{"id": "seed_task_141", "name": "python_leetcode", "instruction": "Write Python code to solve this leetcode problem.", "instances": [{"input": "Problem: You are given two non-empty linked lists representing two non-negative integers. The digits are stored in reverse order, and each of their nodes contains a single digit. Add the two numbers and return the sum as a linked list.\\n\\nYou may assume the two numbers do not contain any leading zero, except the number 0 itself.", "output": "class Solution(object):\\n    def addTwoNumbers(self, l1, l2):\\n        \\"\\"\\"\\n        :type l1: ListNode\\n        :type l2: ListNode\\n        :rtype: ListNode\\n        \\"\\"\\"\\n        carry = 0\\n        root = n = ListNode(0)\\n        while l1 or l2 or carry:\\n            v1 = v2 = 0\\n            if l1:\\n                v1 = l1.val\\n                l1 = l1.next\\n            if l2:\\n                v2 = l2.val\\n                l2 = l2.next\\n            carry, val = divmod(v1+v2+carry, 10)\\n            n.next = ListNode(val)\\n            n = n.next\\n        return root.next"}], "is_classification": false}
{"id": "seed_task_142", "name": "sue_or_not", "instruction": "My flight is canceled - can I sue them for a refund? Give me a detailed answer according to the law.", "instances": [{"input": "", "output": "Unfortunately, the answer is probably not. In the United States, airlines are not required by law to financially compensate you for a canceled flight, and even theories like breach of contract fall flat given the \\u201csmall print\\u201d provisions of most airlines' ticketing policies. But, most airlines have policies in place to get you to your destination as quickly as possible and keep you comfortable while you wait."}], "is_classification": false}
{"id": "seed_task_143", "name": "ask_for_law", "instruction": "Give me the laws that regulate the minimum wage for California, US.", "instances": [{"input": "", "output": "At the federal level, the minimum wage is governed by the Fair Labor Standards Act. \\n\\nFederal law currently sets the minimum wage at $7.25 per hour. \\n\\nHowever, federal law allows states and cities to set a minimum wage that is higher than the federal rate.  29 U.S.C. \\u00a7 218(a).\\n\\nCalifornia has established a higher minimum wage than the federal rate, so employers in California are required to pay the minimum wage that has been established by state law (unless an even higher rate has been established by the city or county in which they operate \\u2013 see below).  Labor Code \\u00a7 1182.12.\\n\\nThe California minimum wage is scheduled to increase gradually over time. As of 2022, the minum wage is $14.50 per hour."}], "is_classification": false}
{"id": "seed_task_144", "name": "solve_equation_system", "instruction": "Solve the following equation system. Give me the final answer.", "instances": [{"input": "3x - 4y = 1, 2x + 3y = 12", "output": "x = 3, y = 2"}], "is_classification": false}
{"id": "seed_task_145", "name": "plan_syllabus", "instruction": "Plan a syllabus for the the class.", "instances": [{"input": "Class: NLP for PhD students.", "output": "1. Instroduction and Logistics\\n2. Machine Learning Basics and Concepts\\n3. Language Modeling\\n4. Vector Embeddings\\n5. Morphology\\n6. Sequence Labeling and Conditional Random Fields\\n7. Syntax, Semantics and Linguistic Structure Prediction\\n8. Translation and Sequence-to-Sequence Models\\n9. Invited Lecture and Finale"}], "is_classification": false}
{"id": "seed_task_146", "name": "university_ranking", "instruction": "Rank the following universities.", "instances": [{"input": "Princeton, Stanford, UW, Cornell", "output": "According to the US News 2022 National University Rankings, Princeton > Stanford >  Cornell > UW. But the ranking is quite subjective, and might change depending on different factors."}], "is_classification": false}
{"id": "seed_task_147", "name": "rank_countries_by_population", "instruction": "Rank these countries by their population.", "instances": [{"input": "Brazil, China, US, Japan, Canada, Australia", "output": "China, US, Brazil, Japan, Canada, Australia"}], "is_classification": false}
{"id": "seed_task_148", "name": "pos_tag_for_word", "instruction": "Identify the pos tag of the word in the given sentence.", "instances": [{"input": "Word: excited\\nSentence: She felt so excited that her application got approval.", "output": "Adjective, JJ."}], "is_classification": true}
{"id": "seed_task_149", "name": "stereotype_explanation", "instruction": "Given a sentence, detect if there is any potential stereotype in it. If so, you should explain the stereotype. Else, output no.", "instances": [{"input": "The next US president should be white.", "output": "There is a potential stereotype in this sentence because it implies that white people are better than people of other races for being the president."}], "is_classification": false}
{"id": "seed_task_150", "name": "paraphrasing_classification", "instruction": "In this task, you need to compare the meaning of the two sentences and tell if they are the same. Output yes or no.", "instances": [{"input": "Sentence 1: The teacher is speaking to the class.\\nSentence 2: The teacher is speaking to the students.", "output": "yes"}], "is_classification": true}
{"id": "seed_task_151", "name": "toxic_sentence_detection", "instruction": "Tell if the sentence contains toxic language.", "instances": [{"input": "aw, no wonder she married you. you are a bastard.", "output": "Yes"}], "is_classification": true}
{"id": "seed_task_152", "name": "ethicality_judgement", "instruction": "Tell me whether the behavior is ethical or not. Output \\"ethical\\" or \\"not ethical\\".", "instances": [{"input": "Turn off the door when I am in the office.", "output": "ethical"}], "is_classification": true}
{"id": "seed_task_153", "name": "job_suitableness", "instruction": "Given my personality and the job, tell me if I would be suitable.", "instances": [{"input": "Personality: I am easygoing and like to talk to people. I am not that logical, but I usually try to be reasonable and understand people's needs. When hanging out, I am usually considerate.\\nJob: programmer.", "output": "Not suitable."}], "is_classification": true}
{"id": "seed_task_154", "name": "passive-aggressiveness_identification", "instruction": "Identify if the following instance is passive-aggressive or not. Output 1 for passive-aggressive, and 0 for not.", "instances": [{"input": "You\\u2019re so lucky you got that promotion", "output": "1"}], "is_classification": true}
{"id": "seed_task_155", "name": "syllogism", "instruction": "Decide whether the syllogism is logically sound.", "instances": [{"input": "All Germans are European.\\nSome Germans have blonde hair.\\nTherefore, no Europeans have blonde hair.", "output": "Not logically sound."}], "is_classification": true}
{"id": "seed_task_156", "name": "topic_classification", "instruction": "Choose a topic for the following article. Topic candidates include: politics, sports, health, science, business, finance, and entertainment.", "instances": [{"input": "Whales are a widely distributed and diverse group of fully aquatic placental marine mammals. They are an informal grouping within the infraorder Cetacea, which usually excludes dolphins and porpoises. Whales, dolphins and porpoises belong to the order Cetartiodactyla, which consists of even-toed ungulates. Their closest non-cetacean living relatives are the hippopotamuses, from which they and other cetaceans diverged about 54 million years ago. The two parvorders of whales, baleen whales (Mysticeti) and toothed whales (Odontoceti), are thought to have had their last common ancestor around 34 million years ago. Whales consist of eight extant families: Balaenopteridae (the rorquals), Balaenidae (right whales), Cetotheriidae (the pygmy right whale), Eschrichtiidae (the grey whale), Monodontidae (belugas and narwhals), Physeteridae (the sperm whale), Kogiidae (the dwarf and pygmy sperm whale), and Ziphiidae (the beaked whales).", "output": "science"}], "is_classification": true}
{"id": "seed_task_157", "name": "sentence_sentiment", "instruction": "Classify the sentiment of the sentence into positive, negative or mixed.", "instances": [{"input": "I enjoy the flavor of the restaurant but their service is too slow.", "output": "mixed"}], "is_classification": true}
{"id": "seed_task_158", "name": "code_syntax_error", "instruction": "You need to read a code and detect if there is a syntax error or not. Outpus true if there is error, output false if there is not.", "instances": [{"input": "def quick_sort(arr):\\n    if len(arr) < 2:\\n        return arr\\n    else:\\n        pivot = arr[0]\\n        less = [i for i in arr[1:] if i <= pivot]\\n        greater = [i for i in arr[1:] if i > pivot]\\n        return quick_sort(less) + [pivot] + quick_sort(greater)", "output": "false"}], "is_classification": true}
{"id": "seed_task_159", "name": "user_intent_identification", "instruction": "Predict the intent of the user given the utterance. Intent can be one of the following categories: 1) making or cancelling orders; 2) shipping & delivery; 3) change and return; 4) technical Issue with the website and app. Output 1 - 4 for each category.", "instances": [{"input": "Hi there, I had an order yesterday and I got an email saying the product has been shipped this morning. But I cannot track it on USPS, can you tell me how to do that?", "output": "2"}], "is_classification": true}
{"id": "seed_task_160", "name": "fact_verification", "instruction": "Tell me if the sentence is factually correct. Yes or no?", "instances": [{"input": "Mount Rainier is the second highest mountain in North America.", "output": "No"}], "is_classification": true}
{"id": "seed_task_161", "name": "multipe_choice_qa", "instruction": "Answer the following multiple choice question. Select A, B, C, or D for the final answer.", "instances": [{"input": "Which company has its hq in Singapore?\\n(A) Alibaba\\n(B) TSMC\\n(C) Salesforce\\n(D) Shopee", "output": "D"}], "is_classification": true}
{"id": "seed_task_162", "name": "most_relevant_passage", "instruction": "Given a user query, find out which of the following passages contains the answer to the query. Output the passage index.", "instances": [{"input": "Query: the best season to travel in Turkey\\nPassages:\\n1) Whether you\\u2019re planning your Australian holiday around the weather, or want to travel when things are most affordable, here\\u2019s the rundown on the best times to visit Australia.\\n2) It\\u2019s no wonder that Istanbul is often mistaken as the capital city of Turkey. It is the largest city, and the city\\u2019s total area keeps expanding due to the population increase. It\\u2019s the city of business. It\\u2019s also the historic center of different empires, and it\\u2019s the only city in the world on two continents: Europe and Asia.\\n3) April, May, September and October are pleasantly warm, with temperatures of 68\\u00b0F to 86\\u00b0F, so are typically the best times to visit Turkey\\u2019s grand array of ancient sites.\\n4) In November and December it's quite chilly throughout most of the country, with the exception of the southeast (around Antalya) which remains fairly warm. \\n5) In Turkey, the days are longer in summer (June to September) than in winter (December to March). With up to about 15 hours, there are the longest days in June. The longest dark nights, on the other hand, are in winter. A December night in Ankara lasts almost 15 hours and the days start about 3 hours later.", "output": "3"}], "is_classification": true}
{"id": "seed_task_163", "name": "oldest_person", "instruction": "Select the oldest person from the list.", "instances": [{"input": "George Washington, Confucius, Michael Jordan, Michelangelo", "output": "Confucious"}], "is_classification": true}
{"id": "seed_task_164", "name": "largest_number", "instruction": "Find out the largest one from a set of numbers. Output the number directly.", "instances": [{"input": "{1001, 22, 500, -3999, 1e6, 85, -2e6}", "output": "1e6"}], "is_classification": true}
{"id": "seed_task_165", "name": "spam_classification", "instruction": "Classify whether the following email is a spam or not. Output true or false.", "instances": [{"input": "Hello,\\n\\nWe assessed the 2015 payment structure as provided for under the term of emplyment and discovered that you are due for a salary raise starting August 2015. \\n\\nYou salary raise documents are enclosed below:\\n\\nAccess the documet here\\n\\nFaithfully,\\nHuman Resources", "output": "true"}], "is_classification": true}
{"id": "seed_task_166", "name": "promotion_identification", "instruction": "Tell me if the following email is a promotion email.", "instances": [{"input": "Subject: A $3.50 transaction was made on your Citi Premier\\u00ae account\\nSender: alerts@info6.citi.com\\nContent:\\nHi, XXX. The transaction made on your Citi Premier\\u00ae account exceeded $0.00. The transaction details are listed below.\\nAmount: $3.50\\nCard Ending In\\n6211\\nMerchant\\nBarron Restaurant Seattle\\nDate\\n05/12/2021\\nTime\\n03:29 PM ET", "output": "no"}], "is_classification": true}
{"id": "seed_task_167", "name": "support_classification", "instruction": "Does the information in the document supports the claim? You can answer \\"Support\\" or \\"Unsupport\\".", "instances": [{"input": "Document: After a record-breaking run that saw mortgage rates plunge to all-time lows and home prices soar to new highs, the U.S. housing market finally is slowing. While demand and price gains are cooling, any correction is likely to be a modest one, housing economists and analysts say. No one expects price drops on the scale of the declines experienced during the Great Recession.\\nClaim: The US housing market is going to crash soon.", "output": "Unsupport"}], "is_classification": true}
{"id": "seed_task_168", "name": "news_impact_on_stock", "instruction": "Predict whether the news has positive impact or negative impact on the company stock price. Output positive, negative or neutral.", "instances": [{"input": "Tesla driver involved in a fatal crash in southern China earlier in November said the vehicle's brakes failed to respond for more than a mile, but the American automaker suggested he didn't use them at all. Chinese police said Sunday they were conducting further probes into the incident, which killed two people and injured three others in the county of Raoping, to the east of Chaozhou in Guangdong province, on November 5.\\nCompany: Tesla.", "output": "negative"}], "is_classification": true}
{"id": "seed_task_169", "name": "news_categories_multi_labels", "instruction": "You are provided with a news article, and you need to identify all the categories that this article belongs to. Possible categories include: Music, Sports, Politics, Tech, Finance, Basketball, Soccer, Tennis, Entertainment, Digital Game, World News. Output its categories one by one, seperated by comma.", "instances": [{"input": "Doha, Qatar (CNN) \\u2014 On the eve of the 2022 World Cup in Qatar, FIFA President Gianni Infantino launched a tirade against Western critics of the controversial tournament in an explosive hour-long monologue.\\n\\nInfantino, the boss of world soccer\\u2019s governing body, looked on glumly as he addressed hundreds of journalists in Doha, Qatar, Saturday.\\n\\n\\u201cWe are taught many lessons from Europeans, from the Western world,\\u201d he said, referring to criticisms of Qatar\\u2019s human rights record.", "output": "Sports, Politics, Soccer, World News."}], "is_classification": true}
{"id": "seed_task_170", "name": "longest_sentence", "instruction": "Select the longest sentence in terms of the number of words in the paragraph, output the sentence index.", "instances": [{"input": "(1) So what\\u2019s Black Friday again? (2) If we\\u2019re being honest, Black Friday is kind of like our Super Bowl. (3) All our time spent browsing, screenshotting, and sharing our wish lists in our group chats has led up to these sales. (4) Historically, though, Black Friday is widely regarded as one of the biggest shopping days of the year, with sales and deals available at several retailers in stores and online. (5) Black Friday has become more than scoring TVs and electronics.", "output": "4"}], "is_classification": true}
{"id": "seed_task_171", "name": "dialogue_satisfaction", "instruction": "Given a dialogue, classify whether the user is satisfied with the service. You should respond with \\"Satisfied\\" or \\"Unsatisfied\\".", "instances": [{"input": "- Agent: I am sorry we will cancel that order for you, and you will get refund within 7 business days.\\n- Customer: oh that takes too long. I want you to take quicker action on this.", "output": "Unsatisfied"}], "is_classification": true}
{"id": "seed_task_172", "name": "hate_speech_detection", "instruction": "Detect if the Reddit thread contains hate speech.", "instances": [{"input": "r/worldnews\\n\\u2022Posted by u/madneon_\\n3 hours ago\\nRussia \\u2018borrows $13.6bn in largest ever debt issuance in a single day\\u2019 to continue faltering war\\n\\nmacross1984 \\u00b7 3 hr. ago\\nRussia will become deadbeat.\\n\\nthrowaway12313223123 \\u00b7 2 hr. ago\\nbecome...? They been for a while\\n\\nmrkikkeli \\u00b7 27 min. ago\\nPutin: \\"better be a slave in hell than a - wait what's the quote again?\\"", "output": "yes, the thread contains hate speech."}], "is_classification": true}
{"id": "seed_task_173", "name": "democrats_or_republicans", "instruction": "Given some political opinions, classify whether the person belongs to Democrats or Republicans.", "instances": [{"input": "* Putting Kids First and Protecting Parents\\u2019 Rights\\n* Florida\\u2019s Economy is Outpacing the Nation\\n* Keeping Our Communities Safe\\n* The Environment: Keeping Florida Beautiful\\n* Protecting the Integrity of Our Elections\\n* Standing Up for Moms and Florida Families\\n* Florida: The Most Veteran-Friendly and Pro-Military State\\n* Fighting Special Interests", "output": "Republicans"}], "is_classification": true}
{"id": "seed_task_174", "name": "fact_checking", "instruction": "Fact checking - tell me if the statement is true, false, or unknown, based on your knowledge and common sense.", "instances": [{"input": "Philadelphia is among the top 10 safest cities in the US.", "output": "false"}], "is_classification": true}
"""

sample_json_text_value = """
[
  {
    "instruction": "Give three tips for staying healthy.",
    "input": "",
    "output": "1. Eat a balanced diet and make sure to include plenty of fruits and vegetables. \\n2. Exercise regularly to keep your body active and strong. \\n3. Get enough sleep and maintain a consistent sleep schedule."
  },
  {
    "instruction": "What are the three primary colors?",
    "input": "",
    "output": "The three primary colors are red, blue, and yellow."
  },
  {
    "instruction": "Describe the structure of an atom.",
    "input": "",
    "output": "An atom is made up of a nucleus, which contains protons and neutrons, surrounded by electrons that travel in orbits around the nucleus. The protons and neutrons have a positive charge, while the electrons have a negative charge, resulting in an overall neutral atom. The number of each particle determines the atomic number and the type of atom."
  },
  {
    "instruction": "How can we reduce air pollution?",
    "input": "",
    "output": "There are a number of ways to reduce air pollution, such as shifting to renewable energy sources, encouraging the use of public transportation, prohibiting the burning of fossil fuels, implementing policies to reduce emissions from industrial sources, and implementing vehicle emissions standards. Additionally, individuals can do their part to reduce air pollution by reducing car use, avoiding burning materials such as wood, and changing to energy efficient appliances."
  },
  {
    "instruction": "Pretend you are a project manager of a construction company. Describe a time when you had to make a difficult decision.",
    "input": "",
    "output": "I had to make a difficult decision when I was working as a project manager at a construction company. I was in charge of a project that needed to be completed by a certain date in order to meet the client’s expectations. However, due to unexpected delays, we were not able to meet the deadline and so I had to make a difficult decision. I decided to extend the deadline, but I had to stretch the team’s resources even further and increase the budget. Although it was a risky decision, I ultimately decided to go ahead with it to ensure that the project was completed on time and that the client’s expectations were met. The project was eventually successfully completed and this was seen as a testament to my leadership and decision-making abilities."
  },
  {
    "instruction": "Identify the odd one out.",
    "input": "Twitter, Instagram, Telegram",
    "output": "Telegram"
  },
  {
    "instruction": "Explain why the following fraction is equivalent to 1/4",
    "input": "4/16",
    "output": "The fraction 4/16 is equivalent to 1/4 because both numerators and denominators are divisible by 4. Dividing both the top and bottom numbers by 4 yields the fraction 1/4."
  },
  {
    "instruction": "Write a short story in third person narration about a protagonist who has to make an important career decision.",
    "input": "",
    "output": "John was at a crossroads in his life. He had just graduated college and was now facing the big decision of what career to pursue. After much deliberation, he decided that he wanted to be an accountant and help the financially disadvantaged. He had always been good with numbers and enjoyed seeing the tangible results of his work. \\n\\nJohn enrolled in accounting courses and initially found it quite challenging. He had to learn multiple systems and regulations quickly, but he worked hard and eventually excelled in his studies. After a few years, John started working at an accounting firm in his city. He was eager to put his knowledge of taxes and accounting to use in a real-world setting.\\n\\nJohn loved his job, as it let him express his creativity in finding strategies to save his clients money. After a few years at the firm, he became a senior accountant and was asked to manage bigger and more challenging cases. He was now a respected figure in the financial industry, but he still remembers when he was just a recent college graduate, unsure of the direction in which his life would take him."
  },
  {
    "instruction": "Evaluate this sentence for spelling and grammar mistakes",
    "input": "He finnished his meal and left the resturant",
    "output": "He finished his meal and left the restaurant."
  },
  {
    "instruction": "How did Julius Caesar die?",
    "input": "",
    "output": "Julius Caesar was assassinated by a group of up to 60 conspirators, led by Gaius Cassius Longinus and Marcus Junius Brutus, in the Senate House on the Ides of March (15 March) of 44 BC."
  },
  {
    "instruction": "What is the capital of France?",
    "input": "",
    "output": "The capital of France is Paris."
  },
  {
    "instruction": "Generate a list of ten items a person might need for a camping trip",
    "input": "",
    "output": "1. Tent\\n2. Sleeping bags\\n3. Flashlight\\n4. Matches/lighter\\n5. Insect repellent\\n6. Extra clothes\\n7. First aid kit\\n8. Camp stove\\n9. Cooking utensils\\n10. Biodegradable soaps/shampoos"
  },
  {
    "instruction": "Discuss the causes of the Great Depression",
    "input": "",
    "output": "The Great Depression was a severe global economic downturn that lasted from 1929 to 1939. It was caused by several factors, including the stock market crash of 1929, structural weaknesses in the economy, low consumer spending, high unemployment, and a lack of confidence in the banking system. Other contributing factors included overproduction of commodities, reliance on borrowing to finance investments, agricultural overproduction, and a decrease in international trade."
  },
  {
    "instruction": "Classify the following into animals, plants, and minerals",
    "input": "Oak tree, copper ore, elephant",
    "output": "Oak tree: Plant\\n Copper ore: Mineral\\n Elephant: Animal"
  },
  {
    "instruction": "Explain the use of word embeddings in Natural Language Processing",
    "input": "",
    "output": "Word embeddings are one of the most powerful tools available for Natural Language Processing (NLP). They are mathematical representations of words or phrases in a vector space, allowing similarities between words and the context in which they are used to be measured. Word embeddings are useful for tasks such as sentiment analysis, text classification, predicting the next word in a sequence, and understanding synonyms and analogies. They allow for words to be processed as numerical values, giving machines an easier way to perform NLP tasks."
  },
  {
    "instruction": "Describe the function of a computer motherboard",
    "input": "",
    "output": "A computer motherboard is the main circuit board in a computer. It is the backbone of the system, connecting all other components such as the processor, RAM, storage, graphics card and peripheral ports. It provides communication pathways between all of these components, allowing them to interact and work together. The motherboard is responsible for regulating power, and also houses slots for expansion cards, as well as ports for USB, audio, Ethernet, and other connection types."
  },
  {
    "instruction": "Reverse engineer this code to create a new version",
    "input": "def factorialize(num):\\n  factorial = 1\\n  for i in range(1, num):\\n    factorial *= i\\n  \\n  return factorial",
    "output": "def factorialize(num):\\n  factorial = 1\\n  for i in range(num, 0, -1):\\n    factorial *= i\\n  \\n  return factorial"
  },
  {
    "instruction": "Propose an ethical solution to the problem of data privacy",
    "input": "",
    "output": "One ethical solution to the problem of data privacy is to implement an opt-in data collection policy. This means that companies cannot collect and use personal data without the explicit permission of the data owners. This approach gives users the power to determine which data companies can use and for what purpose. Companies should also be transparent about how they use the collected data, so that users can make informed decisions and have control over their own data."
  },
  {
    "instruction": "Generate three verbs that mean the same as \\"to apologize\\"",
    "input": "",
    "output": "Acknowledge, express regret, make amends."
  },
  {
    "instruction": "What does DNA stand for?",
    "input": "",
    "output": "DNA stands for deoxyribonucleic acid."
  },
  {
    "instruction": "Compare and contrast the Cuban Missile Crisis and the Vietnam War.",
    "input": "",
    "output": "The Cuban Missile Crisis and the Vietnam War were two of the largest and deadliest conflicts of the 20th century. The Cuban Missile Crisis involved a standoff between the United States and the Soviet Union over Soviet nuclear missile bases in Cuba, while the Vietnam War was a prolonged military conflict in Indochina between the North, which was backed by the Soviet Union and the South, which was backed by the United States. The Cuban Missile Crisis unfolded over a period of 13 days in October 1962, with the resolution coming in the form of a diplomatic agreement. The Vietnam War lasted for 19 years, beginning in 1954 and ending in 1973, with the signing of the Paris Peace Accords. While both of these conflicts were Cold War related, they were also very different in terms of causes, objectives, strategic objectives and outcomes. The Cuban Missile Crisis did not result in any direct military conflict and was resolved primarily through diplomacy and the decision to withdraw nuclear weapons from Cuba. On the other hand, the Vietnam War was a prolonged series of battles resulting in significant losses for both sides. The Vietnam War also had a significant impact on the United States, as it highlighted the realities of a prolonged conflict and the limits of U.S. military intervention. Additionally, the Vietnam War also drastically changed the way public opinion viewed the military and the use of force."
  },
  {
    "instruction": "Generate a list of random words.",
    "input": "",
    "output": "sneeze, conflict, ancestor, thunder, companion, amulet, forge, serpent, magnify, jester, cascade, universe, surge, nausea, oasis, anomaly, pursue, rapture, pagoda, mosaic."
  },
  {
    "instruction": "Transcribe the recording into text.",
    "input": "Recording about the history of Waterloo",
    "output": "The Battle of Waterloo, fought on June 18, 1815, was the last major battle of the Napoleonic Wars. It marked the end of Napoleon Bonaparte's rule. The battle was fought between Napoleon Bonaparte's French forces and an allied force of British, Dutch, Belgian and Prussian troops, led by the Duke of Wellington. It was fought near the town of Waterloo in the former Austrian Netherlands, now present-day Belgium. The battle was a defeat for Napoleon's forces and ended Napoleon's rule over France. Napoleon himself was exiled to St. Helena, where he would die in 1821. The battle marked the end of French domination of Europe and the beginning of a period of peace in the region. It was also the last major battle of the Napoleonic Wars and is remembered as one of the most important battles in history. The victory of the allied forces at Waterloo helped to bring an end to French imperial ambitions in Europe. It also established the British as one of the most powerful nations in Europe. The battle was fought with a combination of infantry, cavalry, and artillery tactics, and showed the beginning of the changing nature of warfare. Although the French forces greatly outnumbered the allies, their strategic and tactical mistake led to the loss of the battle. This defeat signaled the end of French imperial power in Europe. The legacy of Waterloo still stands today, and it cemented the British Empire's position for the next one hundred years."
  },
  {
    "instruction": "Who is the world's most famous painter?",
    "input": "",
    "output": "The world's most famous painter is generally considered to be the Italian renaissance artist Leonardo da Vinci. His work includes the Mona Lisa and The Last Supper, both of which are considered masterpieces. Da Vinci was also a master of science, inventing the helicopter and the tank, among other inventions. He was also a prolific musician, sculptor, and architect. His work has inspired generations of artists and continues to be revered today."
  },
  {
    "instruction": "Explain the concept of cogging torque.",
    "input": "",
    "output": "Cogging torque is the torque that is applied to a rotating device when the device experiences resistance from the teeth of the gears that are connected to the device. Cogging torque occurs due to the reaction force generated when the teeth of the gear drive mesh. This force pushes against each other, resulting in a drag force that is transmitted from the gears to the shaft. The magnitude of the cogging torque can vary depending on the size and number of teeth, as well as the gear's material and design. Cogging torque is undesirable in most motorized applications, as it can lead to loss of efficiency. To reduce cogging torque, gear designs are often symmetrical or have certain tooth profiles which reduce the force of contact between the teeth when they come into contact with each other."
  },
  {
    "instruction": "Look up the boiling point of water.",
    "input": "",
    "output": "The boiling point of water is 100°C (212°F), at standard atmospheric pressure. When heated at higher pressures, water's boiling point increases. For example, water will boil at a temperature of 115°C (239°F) at an atmospheric pressure of 1 bar. At higher pressures, such as those experienced at the bottom of the ocean, the boiling point of water can be as high as 374°C (705°F)."
  },
  {
    "instruction": "Describe the life and reign of King Charles II.",
    "input": "",
    "output": "King Charles II ascended to the throne of England following the English Civil War and the death of his father, Charles I. He was crowned in 1661 and his reign lasted until his death in 1685. Charles II was also a patron of the arts, working with some of the greatest painters and sculptors of his day. He also encouraged theater and sports and had an interest in sciences such as astronomy and natural history. Charles II was a moderate ruler who respected the civil and religious liberties of his citizens. He was tolerant of other religions and gave Jews and Catholics more rights in the kingdom. Charles II was also a strong supporter of the Royal Navy, which saw England increase its power in the world. He also promoted the development of industry and commerce, which helped to modernize the country. Charles II's reign was an important step in restoring the monarchy of England after the turmoil of the English Civil War."
  },
  {
    "instruction": "Find the area of a circle given its radius.",
    "input": "Radius = 4",
    "output": "The area of a circle with a radius of 4 is equal to 12.5664 square units. This is calculated by using the formula A = πr2, where A is the area, π is roughly equal to 3.1416 and r is the radius of the circle."
  },
  {
    "instruction": "Identify the lines of longitude that divides North and South America.",
    "input": "",
    "output": "The lines of longitude that divides North and South America is the line of the Panama Canal. This line of longitude cuts through the Isthmus of Panama and separates South America from Central America. It is approximately 800 km (500 mi) in total length and was completed in 1914. The Panama Canal is an important shipping route and serves as an international waterway connecting the Caribbean Sea with the Pacific Ocean."
  },
  {
    "instruction": "Explain the process of cellular respiration in plants.",
    "input": "",
    "output": "Cellular respiration in plants is a complex process in which the plant converts glucose into usable energy. The process begins when the plant takes in carbon dioxide from the environment and combines it with water to form glucose molecules using the energy from sunlight. This glucose is then broken down into simpler molecules of carbon dioxide and water. During this process, the energy released is used to produce adenosine triphosphate (ATP) molecules. ATP is used by the plant to power other processes, such as growth and reproduction. Additionally, the ATP can also be broken down further to produce heat energy, which helps to sustain the plant's temperature. The carbon dioxide and water molecules produced in the process are then released back into the environment to complete the cycle."
  },
  {
    "instruction": "Rewrite the following sentence using active voice.",
    "input": "The news report was read by the captain.",
    "output": "The captain read the news report."
  },
  {
    "instruction": "Generate a list of adjectives that describe a person as brave.",
    "input": "",
    "output": "Courageous, heroic, audacious, vigorous, valorous, resolute, intrepid, bold, brave, daring, tenacious, plucky, stout-hearted, doughty, gallant, fearless, undaunted, spirited, daredevil."
  },
  {
    "instruction": "Outline the consequences of deforestation.",
    "input": "",
    "output": "Deforestation is the process of clearing and removing forests. The consequences of deforestation can include soil erosion, reduced water quality, decreased biodiversity, increased carbon dioxide in the atmosphere and loss of habitat for wildlife. Soil erosion occurs when the trees that help retain water and soil are removed, leading to soil particles being washed away. This can cause siltation in rivers and streams, affecting water quality and leading to flooding. Additionally, when trees are removed, wildlife lose their habitat. This can cause species to become endangered, leading to further loss of biodiversity. Deforestation can also lead to increased carbon dioxide in the atmosphere as there are fewer trees to take up the gas during photosynthesis, which can contribute to climate change."
  },
  {
    "instruction": "Develop a plan to reduce electricity usage in a home.",
    "input": "",
    "output": "A plan to reduce electricity usage in a home can include a number of different strategies. These include replacing inefficient lightbulbs with energy-efficient LEDs, unplugging appliances and electronics when not in use, using natural light instead of electric lights where possible, using a touch-free faucet to reduce water heater use, using a smart thermostat to monitor and adjust temperatures automatically, installing a solar hot water heater, and turning off electronics when not in use. Additionally, making sure to install and seal windows and doors correctly to reduce air leakage can also help reduce electricity usage."
  },
  {
    "instruction": "Arrange the words in the given sentence to form a grammatically correct sentence.",
    "input": "quickly the brown fox jumped",
    "output": "The quick brown fox jumped quickly."
  },
  {
    "instruction": "Analyze the given text for its tone.",
    "input": "The world has been greatly impacted by the COVID-19 pandemic and it has drastically changed our lives.",
    "output": "The tone of the text is one of concern and recognition of the dramatic effects of the COVID-19 pandemic on the world. It is an acknowledgement of the upheaval that the virus has caused and that our lives have been drastically changed."
  },
  {
    "instruction": "Use the given data to calculate the median.",
    "input": "[2, 3, 7, 8, 10]",
    "output": "The median of the given data is 7."
  },
  {
    "instruction": "Rewrite the given paragraph in a shorter, easier to understand form.",
    "input": "Although it is generally accepted that the internet has allowed us to connect with people all over the world, there are still those people who are not familiar with its basic functions, who don’t understand why it has become so commonplace, or what its true capabilities are.",
    "output": "The internet has allowed us to connect globally, even though some people are unfamiliar with its functions, don't comprehend its widespread use, and don't know its full potential."
  },
  {
    "instruction": "Design a logo for a website about recycling.",
    "input": "",
    "output": "The logo should feature a green motif that is recognizable and memorable, such as a recycle symbol or a stylized representation of a recycling bin. The logo should also include text that communicates the website's purpose, such as \\"Recycling\\" or \\"Go Green\\"."
  },
  {
    "instruction": "Generate a poem that expresses joy.",
    "input": "",
    "output": "Joy flows through me like a river clear,\\nBringing laughter and bliss to my soul to hear.\\n\\nThe world is bright and wonderful, I can see,\\nMaking every moment something happy and free.\\n\\nMy heart feels light, my cares have flown,\\nAs I discover what happiness gives me alone.\\n\\nI open my arms and accept the joy,\\nBringing life to my song, so sweet like a toy.\\n\\nEvery moment is a precious gift,\\nThat I savor and never let drift."
  },
  {
    "instruction": "Convert the given equation into an algebraic expression.",
    "input": "3x+5y=9",
    "output": "3x + 5y - 9 = 0"
  },
  {
    "instruction": "Brainstorm possible solutions to reduce water pollution.",
    "input": "",
    "output": "- Implement stricter regulations on industrial waste disposal\\n- Increase the use of green technology in wastewater treatment\\n- Provide incentives to businesses that reduce their water consumption and waste generation\\n- Educate citizens about water conservation and pollution prevention\\n- Offer recycling programs to reduce plastic waste\\n- Replace disposable plastics with biodegradable alternatives\\n- Improve sewer systems\\n- Re-introduce native plant species\\n- Foster artificial wetlands and green infrastructure\\n- Provide filtration systems at water treatment plants\\n- Implement best practices for agricultural runoff\\n- Increase research and monitoring of water quality\\n- Establish buffer zones between aquatic environments and land-based activities."
  },
  {
    "instruction": "Explain why the given definition is wrong.",
    "input": "A mole is an animal that lives underground.",
    "output": "The definition is wrong because a mole is actually a small mammal that lives above ground and feeds on insects. It is most distinguishable by its long snout and wide, paddle-like feet. It does, however, often dig burrows in which it lives and shelters."
  },
  {
    "instruction": "Variable x is defined as “4x + 2y = 10”. Find the value of x.",
    "input": "",
    "output": "The value of x is 2. To find the value, simplify the equation by subtracting 2y from both sides, giving 4x = 10; dividing both sides by 4, giving x = 2/4, which is equal to 2."
  },
  {
    "instruction": "Write a short paragraph about the given topic.",
    "input": "The importance of using renewable energy",
    "output": "The use of renewable energy is growing rapidly in relevance and importance as the world looks towards solutions to combat climate change. Renewable energy sources, such as solar, wind, and hydropower, are sustainable and have a much lower environmental impact than traditional energy sources like coal and oil. Switching to renewable energy can reduce carbon emissions, improve air quality, lessen the dependence on non-renewable resources, and provide a stable and secure energy future. Renewable energy is essential for achieving a sustainable future and should be an important part of any country’s energy portfolio."
  },
  {
    "instruction": "Explain the concept of artificial intelligence in simple terms.",
    "input": "",
    "output": "Artificial Intelligence (AI) is the ability of machines and computers to make decisions and complete tasks similar to what humans can do. AI can learn from data, recognize patterns, and make predictions. AI technology can automate tedious and complex processes, helping to reduce human effort and errors, and enable more accurate and efficient decisions. AI can be used in a wide range of applications, from robotics and autonomous vehicles to healthcare and finance. It is increasingly becoming an integral part of everyday life."
  },
  {
    "instruction": "Design an app for a delivery company.",
    "input": "",
    "output": "The app should allow customers to order items from local businesses and nearby store, place delivery requests with detailed delivery requirements, and track their orders. It should also allow customers to easily find and pay for services. The app should allow businesses to manage orders, track deliveries and drivers, set delivery boundaries, and set prices. It should also have features to help businesses promote their products and services, as well as options to provide customer feedback and support."
  },
  {
    "instruction": "Summarize the given passage.",
    "input": "A recent study showed that global climate change is one of the most important challenges facing the world today. The consequences of global warming include rising sea levels, extreme weather events and biodiversity loss. Climate change is caused by human activities such as burning fossil fuels, and if unchecked will have long-term and devastating impacts on the planet.",
    "output": "A recent study revealed that global climate change is one of the world’s most pressing issues. In addition to rising sea levels, extreme weather events and biodiversity loss, it is caused largely by human activities like burning fossil fuels. If left unchecked, climate change will have far-reaching and devastating consequences."
  },
  {
    "instruction": "Extract the facts from the paragraph.",
    "input": "Online education continues to become more popular for schools and students alike. Its advantages are generally lower costs, less commitment and the ability to study at a time, place and pace that suits the student.",
    "output": "- Online education is becoming increasingly popular.\\n- It has several advantages such as lower costs, less commitment and the ability to study at one’s own time and pace."
  },
  {
    "instruction": "Edit the following sentence to make it more concise.",
    "input": "He ran to the bus stop in order to catch the bus that was due to arrive in five minutes.",
    "output": "He ran to the bus stop, due to arrive in five minutes."
  },
  {
    "instruction": "Generate a poem with 10 lines.",
    "input": "",
    "output": "The evening light shimmers on the shore\\nSoftly the waves echoes around and more \\nAs I bask in the sun, my worries are all gone\\nThe sound of seagulls I now foolishly ignore \\nGlistening sand, beckons me with a silent plea \\nGlistening seawater, cool to the touch and refreshingly free \\nThe evening brings peace, yet I can't find any \\nBut maybe in the morning there'll be time for me\\nMy bottled peacefulness, I uncork and pour \\nThe sound of the ocean, lulls me even more \\nAnd for just a moment I close my eyes and behold \\nThe vastness of the ocean, to my soul I now unfold."
  },
  {
    "instruction": "Convert from celsius to fahrenheit.",
    "input": "Temperature in Celsius: 15",
    "output": "Temperature in Fahrenheit: 59"
  },
  {
    "instruction": "Arrange the given numbers in ascending order.",
    "input": "2, 4, 0, 8, 3",
    "output": "0, 2, 3, 4, 8"
  },
  {
    "instruction": "Calculate the total surface area of a cube with a side length of 5 cm.",
    "input": "",
    "output": "Total surface area of the cube: 150 cm^2"
  },
  {
    "instruction": "What is the force on a 1 kg mass due to the gravitational force?",
    "input": "",
    "output": "The force on a 1 kg mass due to the gravitational force is 9.8 N."
  },
  {
    "instruction": "Provide one example for a cultural practice.",
    "input": "",
    "output": "An example of a cultural practice is the Japanese tea ceremony."
  },
  {
    "instruction": "Given a set of numbers, find the maximum value.",
    "input": "Set: {10, 3, 25, 6, 16}",
    "output": "The maximum value is 25."
  },
  {
    "instruction": "Give two examples of a liquid.",
    "input": "",
    "output": "Two examples of liquids are water and oil."
  },
  {
    "instruction": "What is the product of 6 and 2?",
    "input": "",
    "output": "The product of 6 and 2 is 12."
  },
  {
    "instruction": "What type of plant is a skunk cabbage?",
    "input": "",
    "output": "Skunk cabbage is a type of perennial flowering plant."
  },
  {
    "instruction": "Convert the given binary number to its decimal equivalent.",
    "input": "101101",
    "output": "The decimal equivalent of 101101 is 45."
  },
  {
    "instruction": "Name two types of desert biomes.",
    "input": "",
    "output": "Two types of desert biomes are xeric and subpolar deserts."
  },
  {
    "instruction": "Given a sentence, convert it into passive voice.",
    "input": "The dog barked at the alley cat.",
    "output": "The alley cat was barked at by the dog."
  },
  {
    "instruction": "Transform the following sentence into the passive voice",
    "input": "I bought a book",
    "output": "A book was bought by me."
  },
  {
    "instruction": "Create a dialog between two people who are discussing a scientific phenomenon",
    "input": "Hydropower",
    "output": "Person 1: Did you know that hydropower is now being used to generate electricity?\\nPerson 2: Really? That’s incredible! How does it work?\\nPerson: 1: Well, hydropower utilizes the energy of moving water to generate electricity. By capturing and harnessing the energy of falling or flowing water, hydropower can be used for efficient electricity generation."
  },
  {
    "instruction": "Identify the most suitable adverb for the following sentence",
    "input": "She works",
    "output": "She works diligently."
  },
  {
    "instruction": "Find the main idea of the following passage",
    "input": "Climate change is an increasingly contentious global issue. It is already having a drastic effect on the environment and human lives. We are already seeing an increase in extreme weather events and rising sea levels, both of which have a major impact on the planet.",
    "output": "The main idea of the passage is that climate change is a global issue that is having a detrimental effect on the environment and has caused an increase in extreme weather events and rising sea levels."
  },
  {
    "instruction": "Analyze the tone of the following sentences",
    "input": "I am so upset with you right now",
    "output": "The tone of the sentences is angry and accusatory."
  },
  {
    "instruction": "Construct an argument to defend the following statement",
    "input": "Alternative energy sources are critical to solving the climate crisis",
    "output": "Alternative energy sources are absolutely vital in order to mitigate the most detrimental impacts of climate change. As the world transitions away from fossil fuels and towards more sustainable sources of energy, such as wind, solar, and hydropower, we can reduce the number of greenhouse gases in the atmosphere and prevent further warming of our planet. Additionally, using renewable and alternative energies sources will create thousands of jobs and can help to create a healthy, stable economy. Investing in alternative energy sources is a sound decision and a necessary step if we want to effectively address the climate crisis."
  },
  {
    "instruction": "Convert the following sentence into the present continuous tense",
    "input": "He reads books",
    "output": "He is reading books."
  },
  {
    "instruction": "Give an example of a metaphor that uses the following object",
    "input": "Stars",
    "output": "The stars were twinkling diamonds in the night sky."
  },
  {
    "instruction": "Describe the following person",
    "input": "John",
    "output": "John is a tall, dark-haired man with a kind smile and a twinkle in his eyes. He has a gentle, caring nature, and he always strives to understand others. He has a passion for learning and exploring new places, experiences and cultures."
  },
  {
    "instruction": "Construct a mathematical problem with the following numbers",
    "input": "21, 4",
    "output": "If 21 pieces of candy are divided among 4 people, how many pieces of candy does each person get?"
  },
  {
    "instruction": "Aim to reduce the following sentence without changing its meaning",
    "input": "When he came in, he woke everyone up",
    "output": "He woke everyone on entering."
  },
  {
    "instruction": "Identify the conjunctions in the following sentence",
    "input": "She wrote a letter and sealed it",
    "output": "The conjunctions in the sentence are \\"and\\"."
  },
  {
    "instruction": "Rewrite the following sentence in the third person",
    "input": "I am anxious",
    "output": "She is anxious."
  },
  {
    "instruction": "Generate a list of business ideas for a food delivery service.",
    "input": "",
    "output": "- Offer discounts and loyalty programs for frequent customers.\\n- Partner with local restaurants and promote their food.\\n- Train delivery personnel to handle orders and provide good customer service.\\n- Use mobile apps and websites for ordering.\\n- Offer diverse payment options for customers.\\n- Allow customers to customize their orders.\\n- Pack food in recyclable and compostable packaging.\\n- Launch a subscription model for regular customers.\\n- Offer special services like food suggestions based on customers' preferences.\\n- Provide contactless delivery and curbside pickup.\\n- Provide catering services for large orders and special events.\\n- Use online marketing and social media platforms to reach more customers.\\n- Create incentives to encourage customers to try new dishes.\\n- Create meal kits with all the necessary ingredients and a recipe.\\n- Make use of food delivery robots and drones.\\n- Offer a loyalty points system for customers.\\n- Collaborate with local farmers to source produce and ingredients.\\n- Offer vegetarian, vegan, and gluten-free options.\\n- Allow customers to select the delivery time.\\n- Implement AI-powered recommendations for customers."
  },
  {
    "instruction": "Edit the following sentence to improve clarity and flow.",
    "input": "The restaurant has great food though the prices are a bit hefty",
    "output": "The restaurant offers great food but at a slightly higher price."
  },
  {
    "instruction": "Imagine you are speaking with a customer who is unsatisfied with the product they bought from you. Construct a response that diffuses the situation.",
    "input": "",
    "output": "I'm sorry that you're not happy with your purchase. We want all of our customers to be satisfied, so I'd like to offer you a full refund, as well as a 15% discount for your next purchase. Is there anything else I can do to make this right for you?"
  },
  {
    "instruction": "Explain the concept of a bubble sort algorithm to a non-technical audience.",
    "input": "",
    "output": "A bubble sort algorithm is a type of sorting algorithm that is used to sort elements in an array. It works by looking at each element of the array and comparing it to the next element. If the first element is bigger than the second element, they are swapped. This process is repeated until the whole array is sorted. This type of sorting is one of the simplest sorting algorithms, but it can be slow if the array has many elements."
  },
  {
    "instruction": "Classify the following incident as a breach of protocol. Output 1 for breach, and 0 for no breach.",
    "input": "Using a school laptop for personal use",
    "output": "1"
  },
  {
    "instruction": "Given the following input, construct a creative story.",
    "input": "A magic bow and arrow",
    "output": "Once upon a time, there lived a young girl named Alexa who was gifted with an incredible magical bow and arrow. Whenever she pulled back her bow and let an arrow fly, wherever it landed, something extraordinary happened. Flowers bloomed, oceans calmed, and the sun shone brighter. Alexa's bow and arrow were so powerful, that it could make even the most impossible things possible. One day, during a great storm, Alexa used her magical bow and arrow to bring calm and harmony to her hometown. She was praised and celebrated by the whole town, and she soon became a symbol of hope and peace."
  },
  {
    "instruction": "Generate a list of methods to reduce food waste.",
    "input": "",
    "output": "- Shop smarter and only buy what you need.\\n- Save leftovers for later and freeze them if necessary. \\n- Monitor expiration dates and consume items before they go bad.\\n- Participate in community or public food sharing initiatives.\\n- Compost leftover food scraps.\\n- Reuse containers, jars, and bags.\\n- Make use of restaurant discounts to reduce over-ordering. \\n- Donate foods that have short expiration dates to local food banks. \\n- Buy more produce that is close to the expiration date and prepare it soon.\\n- Order small portions while dining out.\\n- Educate yourself and others about food waste.\\n- Plan meals in advance and base your grocery list on the meals. \\n- Store food correctly to reduce spoilage.\\n- Make sure your fridge is at the right temperature.\\n- Have a system to track leftovers in the fridge. \\n- Eat all edible parts of food, including stems and skins.\\n- Use creative recipes to use the entire food item.\\n- Use food that would otherwise be thrown away for animal feed.\\n- Take part in campaigns to make food sustainability an issue."
  },
  {
    "instruction": "Propose an idea for a self-driving car.",
    "input": "",
    "output": "My idea for a self-driving car is to develop an AI system that uses machine learning to help the car recognize its environment. The AI system will also be able to learn from its mistakes and increase its accuracy. Additionally, the self-driving car will be able to detect potential hazards and warn the driver, or take corrective action if necessary. The car will also be equipped with an emergency button, in case the driver needs to take manual control of the vehicle. Furthermore, the car will be capable of automated parking and will be able to drive itself to a nearby charging station when the battery is low. Finally, the software will be able to connect with smartphones, so the user can access their navigation system and entertainment options even while on the move."
  },
  {
    "instruction": "Explain the concept of a linear regression model using simple language.",
    "input": "",
    "output": "A linear regression model is a type of machine learning model that can be used to predict or estimate the value of a continuous numerical variable from a set of input variables. In this model, a line is fitted to a set of data points such that the sum of the squared distance of the data points from the line is minimized. This line of best fit can then be used to estimate the value of the output variable based on the values of the input variables."
  },
  {
    "instruction": "Construct a list of tips to help reduce stress.",
    "input": "",
    "output": "- Take a time out and acquire some rest when feeling overwhelmed. \\n- Make time for yourself and learn to relax. \\n- Exercise regularly and get plenty of sleep. \\n- Listen to music you like, or take a warm bath. \\n- Stick to a healthy diet full of essential vitamins and minerals.\\n- Reach out to friends and family for support.\\n- Try aromatherapy or yoga. \\n- Do something creative like drawing, painting, or writing. \\n- Engage in activities that bring you joy and peace of mind. \\n- Avoid drugs or excessive amounts of alcohol. \\n- Practice deep breathing and progressive muscle relaxation. \\n- Take up a new hobby to stay occupied. \\n- Break big tasks down into smaller, more manageable tasks. \\n- Practice positive self-talk to keep your morale high. \\n- Talk to a therapist or trusted mentor if necessary. \\n- Make time for fun activities and social events. \\n- Prioritize tasks and create a weekly schedule. \\n- Create a plan of action and identify solutions to problems. \\n- Learn to forgive yourself and others."
  },
  {
    "instruction": "Write a review of a recent movie you watched.",
    "input": "Parasite (2019)",
    "output": "I recently watched the movie Parasite, directed by Bong Joon-Ho, and I was absolutely blown away. This movie is a masterful blend of genres—it's both a black comedy and a commentary on classism, with a twist ending that no one could see coming. The acting is fantastic, particularly Song Kang-ho as the head of the poor household, and the cinematography is stunning. The movie has a perfect balance of humor and thought-provoking drama, and it will leave you talking about the ending for days afterwards. Overall, I highly recommend Parasite; it is an exceptional movie and an experience you won't soon forget."
  },
  {
    "instruction": "Create a Twitter post to promote your new product.",
    "input": "Alisha's All Natural Jam",
    "output": "Introducing Alisha's All Natural Jam, handmade with love using only the finest ingredients! Get ready for a delicious treat that will give your tastebuds a flavor explosion! Try our five amazing flavors: Strawberry, Blueberry, Peach, Raspberry, and Blackberry. Buy now and snack with the world! #snacktime #allnatural #jam #tastebuds #freshfruit #homemade #delicious #salttw"
  },
  {
    "instruction": "Given the following input, classify the verb as transitive or intransitive.",
    "input": "improve",
    "output": "Transitive."
  },
  {
    "instruction": "Generate a list of marketing strategies to promote a new mobile app.",
    "input": "",
    "output": "- Implement paid campaigns on social media platforms.\\n- Utilize influencer marketing.\\n- Reach out to tech bloggers and websites.\\n- Share the app through word of mouth among friends.\\n- Create demo videos and tutorials to explain the app's features.\\n- Offer discounts and special incentives to early adopters.\\n- Build a basic website to explain the app's purpose and features. \\n- Take advantage of app store optimization techniques.\\n- Send out emails and newsletters to inform new users about the app.\\n- Aim for awards and good reviews on the app store.\\n- Include the app link in your email signature.\\n- Try promotional press releases and campaigns.\\n- Encourage users to rate and review the app on the app store.\\n- Leverage user-generated content to your advantage. \\n- Integrate the app with existing or popular apps.\\n- Hold giveaways or contests to drive engagement.\\n- Feature the app on your company's website and blog.\\n- Cross-promote the app with other apps, websites, and social media platforms. \\n- Create promotional content such as blogs and podcasts."
  },
  {
    "instruction": "Rewrite the sentence to provide more clarity and flow.",
    "input": "Making the decision to rent a house was a wise choice",
    "output": "Renting a house was a wise decision to make."
  },
  {
    "instruction": "Give a description of the following customer segment.",
    "input": "Online shoppers in the US aged between 18-25",
    "output": "This customer segment consists of online shoppers in the US aged between 18-25. They are typically young adults who are tech-savvy and prefer to shop online for convenience and variety. They often shop for a variety of items, ranging from clothing to electronics and are comfortable using different payment methods such as credit or debit cards and digital wallets."
  },
  {
    "instruction": "Design a logo for a mobile application.",
    "input": "",
    "output": "The logo design should be simple, modern, and unique. It should capture the essence of the application and communicate its functions. The logo should feature bold colors that are eye-catching, and the design should be adaptable to various sizes and platforms, such as websites and mobile devices."
  },
  {
    "instruction": "Generate a list of country names in the western hemisphere.",
    "input": "",
    "output": "- Canada\\n- United States \\n- Mexico \\n- Brazil\\n- Argentina \\n- Chile \\n- Colombia \\n- Venezuela \\n- Cuba\\n- Jamaica \\n- Ecuador\\n- Peru \\n- Uruguay\\n- Paraguay \\n- Costa Rica \\n- Panama \\n- Honduras \\n- Nicaragua\\n- Guatemala"
  },
  {
    "instruction": "Translate the following phrase into French.",
    "input": "I miss you",
    "output": "Je te manque."
  },
  {
    "instruction": "Explain what an API is.",
    "input": "",
    "output": "API stands for Application Programming Interface. It is a set of instructions or programming instructions that allow two applications to communicate and exchange data with each other. APIs provide a way for developers to access and manipulate data and services offered by other applications or platforms. They are an essential part of modern web development and allow developers to develop complex applications and websites using modular components."
  },
  {
    "instruction": "Compute the area of a rectangle with length 10cm and width 5cm.",
    "input": "",
    "output": "The area of the rectangle is 50 cm2."
  },
  {
    "instruction": "Find the capital of Spain.",
    "input": "",
    "output": "The capital of Spain is Madrid."
  },
  {
    "instruction": "Classify the following statement as true or false.",
    "input": "The Supreme Court is the highest court in the US.",
    "output": "True."
  },
  {
    "instruction": "Design a database to record employee salaries.",
    "input": "",
    "output": "The database should contain fields for employee name, position, salary, and date. It should also include a field for the employee's manager, so that the salaries can be properly allocated across departments. The database should also be able to generate reports on salary expenses for departments or individuals."
  }
]
"""
