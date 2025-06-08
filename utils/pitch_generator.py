def generate_pitch(notes: str, exit_type: str) -> str:
    """
    Generates a persuasive NLP-style pitch based on user notes and exit strategies.
    Tailored to simulate a real estate closer's tone.
    """
    exit_type = exit_type.lower()
    notes = notes.lower()

    pitch_intro = (
        "I understand the challenge and stress involved in selling a home, "
        "especially when speed and certainty matter. "
        "Imagine a hassle-free process that puts you in control, "
        "closing on your terms with no surprises."
    )

    # Offer type based on exit strategy
    if "cash" in exit_type and "rbp" in exit_type:
        offer = (
            "We offer flexible paths — whether it's a fast cash offer with no inspections, "
            "or a retail partner route that gets you more, even if the house needs some love."
        )
    elif "cash" in exit_type:
        offer = (
            "Our cash offer provides immediate relief: no agents, no showings, no repairs. "
            "Just a quick, clean close when you're ready."
        )
    elif "rbp" in exit_type:
        offer = (
            "Our retail partner program is perfect for sellers looking to maximize value "
            "without the headache of traditional listings. Even if the property needs updates, we’ve got you."
        )
    else:
        offer = (
            "We tailor our approach to your needs — whether that means speed, simplicity, or maximizing your payout."
        )

    # Tailor tone based on notes
    if any(word in notes for word in ["vacant", "urgent", "asap", "foreclosure"]):
        urgency = (
            "Given the urgency, we’re ready to move quickly — even close in under 7 days if needed."
        )
    elif any(word in notes for word in ["roof", "hvac", "plumbing", "foundation", "repairs", "as-is"]):
        urgency = (
            "Since the home may need repairs, our offers are designed to absorb that burden, "
            "so you don’t have to lift a finger."
        )
    else:
        urgency = (
            "We aim to keep things easy, efficient, and profitable — no hidden costs, no stress."
        )

    pitch_close = (
        "Let’s explore this path together. I’ll walk you through every step and make this a win for you. "
        "Would you be open to seeing how that might work?"
    )

    return f'"{pitch_intro} {offer} {urgency} {pitch_close}"'
