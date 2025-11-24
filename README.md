# pandemonium

pandemonium is a high-performance, data-driven discovery API made for osu!. it collects beatmaps, player data, processes them and tries to provide personalized discovery feeds, similarity search and activity-based weighting.

* [discord server](https://discord.gg/9kxkdRzTpf)

## features
* player activity processing
* beatmap embeddings with user tagging (from lazer currently), metadata and other similar ratings,
* personalized discovery feed using vector similarity and weighting
* mode filtering and activity scoring
* similarity search for beatmap and beatmapsets
* (upcoming) fine-tuning controls for discovery
* asynchronous workers and redis queues for scalable processing

## requirements

* Python 3.12+
* PostgreSQL 16+
* Redis
* Qdrant (for vector embeddings)

# development/contribution

contributions are very welcome, as this is a very ambitious project and i'm largely one person. but before starting to work on things, please at least open an issue describing what feature or issue you're working on--or contact me directly!

## licensing

pandemonium is licensed under the MIT license. see the license file for details.