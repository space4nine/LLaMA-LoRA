import fire

from llama_lora.models import get_new_base_model, clear_cache


def main(
    base_model_names: str = "",
):
    '''
    Download and cache base models form Hugging Face.

    :param base_model_names: Names of the base model you want to download, seperated by ",". For example: 'decapoda-research/llama-7b-hf,nomic-ai/gpt4all-j'.
    '''

    assert (
        base_model_names
    ), "Please specify --base_model_names, e.g. --base_model_names='decapoda-research/llama-7b-hf,nomic-ai/gpt4all-j'"

    base_model_names = base_model_names.split(',')
    base_model_names = [name.strip() for name in base_model_names]

    print(f"Base models: {', '.join(base_model_names)}.")

    for name in base_model_names:
        print(f"Preparing {name}...")
        get_new_base_model(name)
        clear_cache()

    print("Done.")

if __name__ == "__main__":
    fire.Fire(main)
