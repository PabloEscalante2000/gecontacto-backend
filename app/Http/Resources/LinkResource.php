<?php

namespace App\Http\Resources;

use Illuminate\Http\Request;
use Illuminate\Http\Resources\Json\JsonResource;

class LinkResource extends JsonResource
{
    /**
     * Transform the resource into an array.
     *
     * @return array<string, mixed>
     */
    public function toArray(Request $request): array
    {
        return [
            "type" => "links",
            "id" => $this->id,
            "attributes" => [
                "phone" => $this->phone,
                "message" => $this->message,
                "created_at" => $this->created_at,
                "updated_at" => $this->updated_at
            ],
            "link" => route("link.get", ["link" => $this->id])
        ];
    }
}
