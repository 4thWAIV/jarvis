// The deck's slides and decisions, read by deck.js and scripts/decide.py.
// Made by examples/packages/decision-deck/new_deck.py. To change the deck,
// edit this data and run new_deck.py on this file again.
window.DECK = {
 "name": "decision-deck-example",
 "version": "1.0.0",
 "purpose": "An example decision deck: three decisions on an office tea service, answered on the page or by voice.",
 "title": "Office tea: decisions",
 "language": "en",
 "slides": [
  {
   "id": "s1",
   "kicker": "Example",
   "title": "Three decisions on the office tea service",
   "say": "This is an example deck. It asks three decisions, and the third is asked only after one answer to the second.",
   "blocks": [
    {
     "lead": "The office wants a proper pot of tea. Three things are left to decide."
    },
    {
     "facts": [
      {
       "n": "3",
       "text": "decisions, one slide each"
      },
      {
       "n": "1",
       "text": "follows from another's answer"
      },
      {
       "n": "0",
       "text": "chosen for you: nothing is pre-selected"
      }
     ]
    },
    {
     "note": "Answer on each decision's slide, or out loud to JARVIS. Both go into results/decisions.json."
    }
   ]
  },
  {
   "id": "s2",
   "kicker": "What we measured",
   "title": "Heating a litre of water",
   "blocks": [
    {
     "picture": "pictures/kettles.svg",
     "alt": "An electric kettle takes 3 minutes, a stovetop kettle 6, a hot water tap none",
     "caption": "Minutes to a litre at the boil, from cold."
    },
    {
     "points": [
      {
       "b": "Electric:",
       "text": "fast, and switches itself off."
      },
      {
       "b": "Stovetop:",
       "text": "needs the hob, and someone watching it."
      },
      {
       "b": "Tap:",
       "text": "instant, but costs the most to fit."
      }
     ]
    }
   ]
  }
 ],
 "decisions": [
  {
   "id": "kettle-kind",
   "short": "Kettle",
   "question": "What heats the water?",
   "context": "The office has a sink and one socket by it, and no hob.",
   "options": [
    {
     "id": "electric",
     "name": "An electric kettle",
     "says": "Three minutes to the boil; it switches itself off.",
     "picture": "pictures/electric-kettle.svg"
    },
    {
     "id": "tap",
     "name": "A hot water tap",
     "says": "Boiling water at once; a plumber fits it.",
     "picture": "pictures/hot-tap.svg"
    }
   ],
   "recommended": "electric",
   "reason": "There is a socket and no plumber booked, and it is quick enough."
  },
  {
   "id": "tea-kinds",
   "short": "Teas",
   "question": "One house blend, or a small selection?",
   "options": [
    {
     "id": "house-blend",
     "name": "One house blend",
     "says": "One tin to keep full. Everyone drinks the same.",
     "picture": "pictures/house-blend.svg"
    },
    {
     "id": "selection",
     "name": "A small selection",
     "says": "A few teas for different tastes. Needs a shelf.",
     "opens": [
      "selection-size"
     ],
     "picture": "pictures/selection.svg"
    },
    {
     "id": "bring-your-own",
     "name": "Bring your own",
     "says": "The office buys none; people keep their own.",
     "picture": "pictures/bring-your-own.svg"
    }
   ],
   "recommended": "selection",
   "reason": "People's tastes differ, and the shelf is already there."
  },
  {
   "id": "selection-size",
   "short": "How many",
   "question": "How many teas in the selection?",
   "options": [
    {
     "id": "three",
     "name": "Three",
     "says": "A black, a green and an herbal.",
     "picture": "pictures/three-kinds.svg"
    },
    {
     "id": "six",
     "name": "Six",
     "says": "Two of each kind; more to keep stocked.",
     "picture": "pictures/six-kinds.svg"
    }
   ],
   "recommended": "three",
   "reason": "Three covers every taste anyone has asked for."
  }
 ]
};
