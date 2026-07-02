from itertools import chain, combinations,product

def get_all_non_empty_subsets(input_list):
    """
    Generate all non-empty subsets of the given input list.

    Args:
        input_list (list): List of strings for which to generate subsets.

    Returns:
        list: A list containing all possible non-empty subset lists.
    """
    # Using itertools.combinations to generate all combinations
    return [subset for r in range(1, len(input_list)+1)
                   for subset in combinations(input_list, r)]

def get_subsets_with_min_size(input_list, min_size):
    """
    Generate all non-empty subsets of the given input list with a minimum size.

    Args:
        input_list (list): List of elements for which to generate subsets.
        min_size (int): The minimum number of elements required in each subset.
                        Defaults to 1 (generating all non-empty subsets).

    Returns:
        list: A list containing all possible subset lists that meet the minimum size requirement.
    """
    n = len(input_list)
    
    # Input validation: Ensure min_size is within a valid range
    if not (1 <= min_size <= n):
        # If min_size is too small or too large, adjust it or return an empty list
        if min_size > n:
            print(f"Warning: Minimum size ({min_size}) is greater than the list size ({n}). Returning empty list.")
            return []
        elif min_size < 1:
            print(f"Warning: Minimum size must be at least 1. Using min_size=1.")
            min_size = 1

    # Using itertools.combinations to generate all combinations
    # The range starts from min_size up to the length of the list (n)
    all_subsets = [list(subset) # Convert tuple to list for consistency
                   for r in range(min_size, n + 1)
                   for subset in combinations(input_list, r)]
    
    return all_subsets


def get_pairwise_subsets(list1, list2):
    """
    Generates subsets (pairs) that contain exactly one element from list1
    and one element from list2.

    Args:
        list1 (list): The first list of elements.
        list2 (list): The second list of elements.

    Returns:
        list: A list containing all possible two-element subsets (pairs).
    """
    # Use itertools.product to generate the Cartesian product of the two lists.
    # The result is an iterator of tuples, where each tuple contains one
    # element from list1 and one element from list2.
    all_pairs = [list(subset) for subset in product(list1, list2)]
    
    return all_pairs

def get_triplewise_subsets(list1, list2, list3):
    """
    Generates subsets (triplets) that contain exactly one element from list1, 
    one from list2, and one from list3.

    Args:
        list1 (list): The first list of elements.
        list2 (list): The second list of elements.
        list3 (list): The third list of elements.

    Returns:
        list: A list containing all possible three-element subsets (triplets).
    """
    # Use itertools.product with all three lists. 
    # The result is an iterator of tuples, where each tuple contains one
    # element from each input list.
    all_triplets = [list(subset) for subset in product(list1, list2, list3)]
    
    return all_triplets

# Example usage
# input_list = ["deep learning", "natural language processing", "sentiment analysis"]
# all_subsets = get_all_non_empty_subsets(input_list)
# print(all_subsets)